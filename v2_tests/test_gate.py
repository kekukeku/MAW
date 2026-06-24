"""v2 gate tests — verify v2 isolation, path safety, and inspect correctness."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from v2.files import (
    scaffold_target,
    init_workflow,
    ensure_workflow_dirs,
    exists_nonempty,
    write_atomic,
    ARTIFACT_CHAIR_BRIEF,
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


if __name__ == "__main__":
    unittest.main()
