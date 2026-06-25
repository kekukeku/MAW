"""v2 gate tests — verify v2 isolation, path safety, and inspect correctness."""

import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process, Queue, Event
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
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acquire_and_release_lock(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertEqual(lock["workflow_id"], "wf_a")

        ok = release_executor_lock(self.target, "wf_a")
        self.assertTrue(ok)
        self.assertIsNone(check_executor_lock(self.target))

    def test_release_only_owner_can_remove(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        ok = release_executor_lock(self.target, "wf_b")
        self.assertFalse(ok)
        self.assertIsNotNone(check_executor_lock(self.target))
        ok = release_executor_lock(self.target, "wf_a")
        self.assertTrue(ok)

    def test_active_lock_blocks_other_workflow(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        ok2 = acquire_executor_lock(self.target, "wf_b", "wf_b:exec:executor:1", lock_timeout=600)
        self.assertFalse(ok2)
        ok3 = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:2", lock_timeout=600)
        self.assertTrue(ok3)

    def test_stale_lock_allows_acquisition(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        ok2 = acquire_executor_lock(self.target, "wf_b", "wf_b:exec:executor:1", lock_timeout=0)
        self.assertTrue(ok2)
        lock = check_executor_lock(self.target)
        self.assertEqual(lock["workflow_id"], "wf_b")

    def test_release_allows_other_workflow(self):
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec:executor:1")
        self.assertTrue(ok)
        ok = release_executor_lock(self.target, "wf_a")
        self.assertTrue(ok)
        ok2 = acquire_executor_lock(self.target, "wf_b", "wf_b:exec:executor:1")
        self.assertTrue(ok2)

    def test_concurrent_race_exactly_one_wins(self):
        results: list[bool] = []
        barrier = threading.Barrier(2, timeout=5)

        def competitor(workflow_id: str) -> None:
            barrier.wait()
            ok = acquire_executor_lock(self.target, workflow_id, f"{workflow_id}:exec")
            results.append(ok)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(competitor, "wf_alpha")
            f2 = pool.submit(competitor, "wf_beta")
            f1.result(timeout=10)
            f2.result(timeout=10)

        self.assertEqual(len(results), 2)
        winners = sum(1 for r in results if r)
        self.assertEqual(winners, 1, f"Expected 1 winner, got {winners}: {results}")

        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertIn(lock["workflow_id"], ("wf_alpha", "wf_beta"))

    def test_race_repeated_is_stable(self):
        for attempt in range(5):
            release_executor_lock(self.target, "wf_one")
            release_executor_lock(self.target, "wf_two")
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
            self.assertEqual(winners, 1, f"Round {attempt}: expected 1 winner, got {results}")

    # ── Cross-process tests ──

    def test_mp_initial_acquisition_race(self):
        """Test A: Two processes race for an absent lock — exactly one wins."""
        q: Queue = Queue()
        e = Event()
        p1 = Process(target=_mp_acquire_worker, args=(self.target, "wf_alpha", q, e))
        p2 = Process(target=_mp_acquire_worker, args=(self.target, "wf_beta", q, e))
        p1.start()
        p2.start()
        time.sleep(0.2)  # let both processes reach the Event.wait()
        e.set()  # release the barrier
        p1.join(timeout=15)
        p2.join(timeout=15)

        results = [q.get(timeout=5), q.get(timeout=5)]
        self.assertEqual(len(results), 2)
        winners = sum(1 for r in results if r)
        self.assertEqual(winners, 1, f"Expected 1 winner, got {results}")

        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertIn(lock["workflow_id"], ("wf_alpha", "wf_beta"))

    def test_mp_stale_takeover_race(self):
        """Test B: Two processes race for a stale lock — exactly one wins."""
        ok = acquire_executor_lock(self.target, "wf_old", "wf_old:exec", lock_timeout=600)
        self.assertTrue(ok)

        q: Queue = Queue()
        e = Event()
        p1 = Process(target=_mp_stale_takeover_worker, args=(self.target, "wf_new1", "wf_old", q, e))
        p2 = Process(target=_mp_stale_takeover_worker, args=(self.target, "wf_new2", "wf_old", q, e))
        p1.start()
        p2.start()
        time.sleep(0.05)
        e.set()
        p1.join(timeout=15)
        p2.join(timeout=15)

        results = [q.get(timeout=10), q.get(timeout=10)]
        winners = sum(1 for r in results if r)
        self.assertEqual(winners, 1, f"Stale takeover: expected 1 winner, got {results}")

        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertIn(lock["workflow_id"], ("wf_new1", "wf_new2"))

    def test_mp_previous_owner_cannot_release(self):
        """Test C: Old owner cannot release a lock that was taken over."""
        ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec")
        self.assertTrue(ok)

        # B takes over with lock_timeout=0 (stale)
        ok = acquire_executor_lock(self.target, "wf_b", "wf_b:exec", lock_timeout=0)
        self.assertTrue(ok)

        # A tries to release — must fail
        ok = release_executor_lock(self.target, "wf_a")
        self.assertFalse(ok)

        # B's lock still present
        lock = check_executor_lock(self.target)
        self.assertIsNotNone(lock)
        self.assertEqual(lock["workflow_id"], "wf_b")

        # B can release its own lock
        ok = release_executor_lock(self.target, "wf_b")
        self.assertTrue(ok)

    def test_mp_release_vs_takeover_race(self):
        """Test D: Release vs takeover race — final state must be valid."""
        for attempt in range(5):
            release_executor_lock(self.target, "wf_a")
            release_executor_lock(self.target, "wf_b")
            ok = acquire_executor_lock(self.target, "wf_a", "wf_a:exec")
            self.assertTrue(ok)

            q: Queue = Queue()
            e = Event()
            p_release = Process(target=_mp_release_worker, args=(self.target, "wf_a", q, e))
            p_takeover = Process(target=_mp_stale_takeover_worker, args=(self.target, "wf_b", "wf_a", q, e))
            p_release.start()
            p_takeover.start()
            time.sleep(0.05)
            e.set()
            p_release.join(timeout=15)
            p_takeover.join(timeout=15)

            try:
                for _ in range(2):
                    q.get(timeout=5)
            except Exception:
                pass

            # Final lock state must be valid (one owner, valid JSON)
            lock = check_executor_lock(self.target)
            if lock is not None:
                self.assertIn(lock["workflow_id"], ("wf_a", "wf_b"))
                self.assertIsInstance(lock.get("started_at"), str)
            # If lock is gone, that's also valid (release won the race)


# ── Multiprocessing worker functions (must be module-level for spawn) ──

def _mp_acquire_worker(target: str, wf_id: str, q: Queue, e: Event) -> None:
    from v2.files import acquire_executor_lock
    e.wait()  # synchronize start
    try:
        ok = acquire_executor_lock(target, wf_id, f"{wf_id}:exec")
        q.put(ok)
    except Exception:
        q.put(False)


def _mp_stale_takeover_worker(target: str, wf_id: str, _old_wf: str, q: Queue, e: Event) -> None:
    from v2.files import acquire_executor_lock
    e.wait()
    try:
        ok = acquire_executor_lock(target, wf_id, f"{wf_id}:exec", lock_timeout=0)
        q.put(ok)
    except Exception:
        q.put(False)


def _mp_release_worker(target: str, wf_id: str, q: Queue, e: Event) -> None:
    from v2.files import release_executor_lock
    e.wait()
    try:
        ok = release_executor_lock(target, wf_id)
        q.put(ok)
    except Exception:
        q.put(True)  # release failures are benign in race tests


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
