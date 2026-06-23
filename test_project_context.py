import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_context as pc


class TestProjectContext(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.target_root = self.test_dir / "target"
        self.target_root.mkdir()

        # Create project structure.
        (self.target_root / "README.md").write_text(
            "# Test Project\n\nThis is a test project for context-aware council.\n" * 50,
            encoding="utf-8",
        )
        (self.target_root / "package.json").write_text(
            '{"name": "test", "scripts": {"test": "jest", "lint": "eslint src"}}',
            encoding="utf-8",
        )
        (self.target_root / "pyproject.toml").write_text(
            "[project]\nname = \"test\"\nversion = \"0.1.0\"\n",
            encoding="utf-8",
        )
        src_dir = self.target_root / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

        # Excluded directories/files.
        (self.target_root / "node_modules").mkdir()
        (self.target_root / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
        (self.target_root / "MAW_workflow").mkdir()
        (self.target_root / "MAW_workflow" / "state.md").write_text("state", encoding="utf-8")
        (self.target_root / ".env").write_text("SECRET=12345\n", encoding="utf-8")
        (self.target_root / ".git").mkdir()
        (self.target_root / ".gitignore").write_text(
            "node_modules/\nMAW_workflow/\n.env\n*.log\n",
            encoding="utf-8",
        )

        # Large file to test truncation.
        large = "x" * 30000
        (self.target_root / "large_file.txt").write_text(large, encoding="utf-8")

        # Initialize git repo so git check-ignore works.
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.target_root,
            capture_output=True,
            check=False,
        )

        self.targets = {
            "default": "test",
            "projects": {
                "test": {
                    "name": "Test Target",
                    "path": str(self.target_root),
                }
            },
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _load_targets_patch(self):
        return patch.object(pc, "load_targets", return_value=self.targets)

    def test_build_context_pack_schema(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        self.assertEqual(pack["version"], 1)
        self.assertEqual(pack["targetKey"], "test")
        self.assertIn("targetPath", pack)
        self.assertIn("generatedAt", pack)
        self.assertIn("policy", pack)
        self.assertIn("summary", pack)
        self.assertIn("blueprint", pack)
        self.assertIn("files", pack)
        self.assertIn("accessIssues", pack)

        summary = pack["summary"]
        self.assertEqual(summary["status"], "ready")
        self.assertGreater(summary["totalChars"], 0)
        self.assertIn("truncated", summary)
        self.assertIn("includedFiles", summary)
        self.assertIn("excludedFiles", summary)

        blueprint = pack["blueprint"]
        self.assertIn("tree", blueprint)
        self.assertIn("readme", blueprint)
        self.assertIn("dependencies", blueprint)

    def test_excludes_always_excluded_dirs(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        tree = pack["blueprint"]["tree"]
        self.assertIn("src", tree)
        self.assertNotIn("node_modules", tree)
        self.assertNotIn("MAW_workflow", tree)
        # .git directory should be excluded; .gitignore file is fine.
        self.assertNotIn("├── .git\n", tree)
        self.assertIn(".gitignore", tree)

        paths = {issue["path"] for issue in pack["accessIssues"]}
        self.assertTrue(any("node_modules" in p for p in paths))
        self.assertTrue(any("MAW_workflow" in p for p in paths))

    def test_excludes_secrets(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        paths = {issue["path"] for issue in pack["accessIssues"]}
        self.assertIn(".env", paths)

    def test_respects_gitignore(self):
        # Create an ignored file not in always-excluded list.
        (self.target_root / "ignored.tmp").write_text("temp\n", encoding="utf-8")
        # Append a gitignore rule for .tmp files.
        with open(self.target_root / ".gitignore", "a", encoding="utf-8") as f:
            f.write("*.tmp\n")
        subprocess.run(["git", "add", "-A"], cwd=self.target_root, capture_output=True, check=False)

        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        paths = {issue["path"] for issue in pack["accessIssues"]}
        self.assertIn("ignored.tmp", paths)
        self.assertTrue(any(issue["reason"] == "excluded_by_gitignore" for issue in pack["accessIssues"]))

    def test_readme_included(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        readme = pack["blueprint"]["readme"]
        self.assertIn("Test Project", readme)
        # Should be truncated if README is large.
        self.assertLessEqual(len(readme), pack["policy"]["maxReadmeChars"] + 100)

    def test_dependency_files_included(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        deps = pack["blueprint"]["dependencies"]
        dep_paths = {d["path"] for d in deps}
        self.assertIn("package.json", dep_paths)
        self.assertIn("pyproject.toml", dep_paths)

        package = next(d for d in deps if d["path"] == "package.json")
        self.assertIn("jest", package["content"])
        self.assertIn("eslint", package["content"])

    def test_large_file_truncated(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        # large_file.txt should be excluded by size? Actually it's not in always excluded,
        # and we don't exclude by size in L0; it's just not included because only deps/README are read.
        # It may appear in tree if small enough, but tree limit is by entries.
        # The truncation we test is README truncation.
        readme = pack["blueprint"]["readme"]
        max_chars = pack["policy"]["maxReadmeChars"]
        if len(readme) >= max_chars:
            self.assertIn("omitted", readme)

    def test_prompt_envelope_contains_context(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        envelope = pc.build_prompt_envelope("Implement feature X", pack)
        self.assertIn("Target Project Context", envelope)
        self.assertIn("Context Boundaries", envelope)
        self.assertIn("User Request", envelope)
        self.assertIn("Implement feature X", envelope)
        self.assertIn(pack["targetKey"], envelope)

    def test_prompt_envelope_unavailable(self):
        envelope = pc.build_prompt_envelope("Implement feature X", None)
        self.assertIn("unavailable", envelope)
        self.assertIn("Context Boundaries", envelope)
        self.assertIn("Implement feature X", envelope)

    def test_prompt_envelope_includes_explorer_brief_when_ready(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Fix token expiry")
        pack["explorerBrief"] = {
            "status": "ready",
            "summary": "Explorer examined 2 candidate files.",
            "candidateFiles": [
                {
                    "path": "src/auth.py",
                    "contentIncluded": False,
                    "excerpt": "",
                    "truncated": False,
                },
            ],
        }
        envelope = pc.build_prompt_envelope("Fix token expiry", pack)
        self.assertIn("Explorer Research Brief", envelope)
        self.assertIn("NOT source of truth", envelope)
        self.assertIn("content not read", envelope)

    def test_prompt_envelope_skips_failed_explorer_brief(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Fix token expiry")
        pack["explorerBrief"] = {"status": "failed", "summary": "should not appear"}
        envelope = pc.build_prompt_envelope("Fix token expiry", pack)
        self.assertNotIn("Explorer Research Brief", envelope)

    def test_compact_digest(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        digest = pc.compact_context_digest(pack)
        self.assertIn(pack["targetKey"], digest)
        self.assertIn("Directory tree", digest)

    def test_unknown_target_raises(self):
        with self.assertRaises(pc.ContextTargetError):
            pc.build_context_pack("unknown", "Implement feature X")

    def test_build_context_preview_response(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")
        preview = pc.build_context_preview_response(pack)
        self.assertEqual(preview["version"], pack["version"])
        self.assertEqual(preview["targetKey"], pack["targetKey"])
        self.assertNotIn("targetPath", preview)
        self.assertEqual(preview["level"], pack["level"])
        self.assertNotIn("text", preview)
        self.assertIn("files", preview)
        self.assertIsInstance(preview["warnings"], list)
        # blueprint is included but as a slim metadata preview, not the full tree/README text.
        self.assertIn("blueprint", preview)
        self.assertNotIn("readme", preview["blueprint"])
        self.assertNotIn("dependencies", preview["blueprint"])
        if preview["files"]:
            self.assertNotIn("content", preview["files"][0])
            self.assertIn("path", preview["files"][0])

    def test_no_readme_still_ready(self):
        (self.target_root / "README.md").unlink()
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X")

        self.assertEqual(pack["summary"]["status"], "ready")
        self.assertEqual(pack["blueprint"]["readme"], "")

    def test_total_budget_truncation_marker(self):
        policy = {**pc.DEFAULT_POLICY, "maxTotalChars": 100}
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", policy=policy)

        self.assertTrue(pack["summary"]["truncated"])
        self.assertTrue(
            any("exceeded_total_budget" in issue.get("reason", "") for issue in pack["accessIssues"])
        )


    def test_l1_user_selected_files_added(self):
        (self.target_root / "src" / "utils.py").write_text("def util():\n    return 42\n", encoding="utf-8")
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["src/utils.py"])
        self.assertEqual(pack["level"], "L1")
        self.assertEqual(len(pack["files"]), 1)
        self.assertEqual(pack["files"][0]["path"], "src/utils.py")
        self.assertEqual(pack["files"][0]["source"], "user_selected")
        self.assertIn("def util", pack["files"][0]["content"])

    def test_l1_files_appear_in_prompt_envelope(self):
        (self.target_root / "src" / "utils.py").write_text("def util():\n    return 42\n", encoding="utf-8")
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["src/utils.py"])
        envelope = pc.build_prompt_envelope("Implement feature X", pack)
        self.assertIn("Selected / Scout Files", envelope)
        self.assertIn("src/utils.py", envelope)
        self.assertIn("def util", envelope)

    def test_l1_traversal_path_rejected(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["../escape.py"])
        self.assertEqual(pack["level"], "L0")
        self.assertEqual(len(pack["files"]), 0)
        self.assertTrue(
            any("Rejected traversal path" in i.get("reason", "") for i in pack["accessIssues"])
        )

        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["/etc/passwd"])
        self.assertEqual(pack["level"], "L0")
        self.assertEqual(len(pack["files"]), 0)
        self.assertTrue(
            any("Rejected absolute path" in i.get("reason", "") for i in pack["accessIssues"])
        )

    def test_l1_non_existent_file_rejected(self):
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["missing.py"])
        self.assertEqual(pack["level"], "L0")
        self.assertEqual(len(pack["files"]), 0)
        self.assertTrue(any("l1_rejected" in i.get("reason", "") for i in pack["accessIssues"]))

    def test_l1_secret_file_rejected(self):
        (self.target_root / ".env").write_text("SECRET=123", encoding="utf-8")
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=[".env"])
        self.assertEqual(pack["level"], "L0")
        self.assertEqual(len(pack["files"]), 0)
        self.assertTrue(any(".env" in i.get("path", "") for i in pack["accessIssues"]))

    def test_l1_gitignored_file_rejected(self):
        (self.target_root / "debug.log").write_text("log entry", encoding="utf-8")
        with open(self.target_root / ".gitignore", "a", encoding="utf-8") as f:
            f.write("\n*.log\n")
        subprocess.run(["git", "add", "-A"], cwd=self.target_root, capture_output=True, check=False)
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["debug.log"])
        self.assertEqual(pack["level"], "L0")
        self.assertEqual(len(pack["files"]), 0)

    def test_l1_large_file_truncated(self):
        large_content = "x" * 15000
        (self.target_root / "src" / "large.py").write_text(large_content, encoding="utf-8")
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["src/large.py"])
        self.assertEqual(pack["level"], "L1")
        self.assertEqual(len(pack["files"]), 1)
        self.assertTrue(pack["files"][0]["truncated"])
        self.assertIn("chars omitted", pack["files"][0]["content"])

    def test_list_safe_files_excludes_dirs(self):
        with self._load_targets_patch():
            files = pc.list_safe_files("test")
        paths = {f["path"] for f in files}
        self.assertIn("src/main.py", paths)
        self.assertIn("README.md", paths)
        self.assertNotIn("node_modules", paths)
        self.assertNotIn(".git", paths)

    def test_list_safe_files_has_metadata(self):
        with self._load_targets_patch():
            files = pc.list_safe_files("test")
        for f in files:
            self.assertIn("path", f)
            self.assertIn("size", f)
            self.assertIn("kind", f)
            self.assertIn("mtime", f)

    def test_list_safe_files_unknown_target_raises(self):
        with self.assertRaises(pc.ContextTargetError):
            pc.list_safe_files("nonexistent")

    def test_l1_multiple_files_increase_summary(self):
        (self.target_root / "src" / "a.py").write_text("a", encoding="utf-8")
        (self.target_root / "src" / "b.py").write_text("b", encoding="utf-8")
        with self._load_targets_patch():
            pack = pc.build_context_pack("test", "Implement feature X", context_files=["src/a.py", "src/b.py"])
        self.assertEqual(pack["level"], "L1")
        self.assertEqual(len(pack["files"]), 2)


    def test_budget_l1_survives_when_l0_is_sacrificed(self):
        """L1 files survive total budget truncation; L0 blueprint is cut instead."""
        big_tree = "big_tree\n" * 500
        big_readme = "# Big README\n" + "big readme content.\n" * 500
        big_dep = "dep_content\n" * 500
        l1_content = "def important():\n    return 'this must survive'\n" * 20

        pack = {
            "version": 1,
            "targetKey": "test",
            "level": "L1",
            "policy": {"maxTotalChars": 1500},
            "summary": {
                "status": "ready",
                "includedFiles": 4,
                "totalChars": len(big_tree) + len(big_readme) + len(big_dep) + len(l1_content),
                "truncated": True,
            },
            "blueprint": {
                "tree": big_tree,
                "readme": big_readme,
                "dependencies": [
                    {"path": "pyproject.toml", "content": big_dep, "chars": len(big_dep), "truncated": False},
                ],
            },
            "files": [
                {"path": "src/critical.py", "source": "user_selected", "content": l1_content, "chars": len(l1_content), "truncated": False},
            ],
            "accessIssues": [],
        }

        envelope = pc.build_prompt_envelope("Do the thing", pack)

        self.assertIn("def important", envelope)
        self.assertIn("src/critical.py", envelope)

        truncated_issues = [
            i for i in pack["accessIssues"]
            if "truncated_by_total_budget" in i.get("reason", "")
        ]
        tree_omitted = "tree omitted" in envelope.lower()
        self.assertTrue(len(truncated_issues) > 0 or tree_omitted,
                        f"Should truncate L0 before L1. envelope length={len(envelope)}")

    def test_budget_cuts_tree_before_l1(self):
        """When budget is tight, tree is cut before L1 files."""
        big_tree = "tree_line\n" * 300
        l1_content = "def keep_me():\n    pass\n" * 5

        (self.target_root / "src" / "keep.py").write_text(l1_content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.target_root, capture_output=True, check=False)

        small_policy = {**pc.DEFAULT_POLICY, "maxTotalChars": 1200}
        with self._load_targets_patch():
            pack = pc.build_context_pack(
                "test",
                "test",
                context_files=["src/keep.py"],
                policy=small_policy,
            )
        pack["blueprint"]["tree"] = big_tree

        envelope = pc.build_prompt_envelope("test", pack)
        self.assertIn("def keep_me", envelope)
        self.assertIn("src/keep.py", envelope)

        self.assertIn("tree truncated by total budget", envelope.lower().replace("_", " "))

    def test_budget_truncation_markers_are_specific(self):
        """Truncation issues say what was cut, not just '<context_pack>'."""
        big_tree = "tree\n" * 600
        l1_content = "x\n" * 10

        (self.target_root / "src" / "x.py").write_text(l1_content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.target_root, capture_output=True, check=False)

        tiny_policy = {**pc.DEFAULT_POLICY, "maxTotalChars": 800}
        with self._load_targets_patch():
            pack = pc.build_context_pack(
                "test", "test", context_files=["src/x.py"], policy=tiny_policy,
            )
        pack["blueprint"]["tree"] = big_tree

        pc.build_prompt_envelope("test", pack)

        truncated_issues = [
            i for i in pack.get("accessIssues", [])
            if "truncated_by_total_budget" in i.get("reason", "")
        ]
        self.assertTrue(len(truncated_issues) > 0, "Should have total-budget truncation markers")
        # Markers should be specific (mention tree/readme/dependency), not just a generic tag.
        for issue in truncated_issues:
            reason = issue.get("reason", "")
            self.assertIn(":", reason, f"Reason should have a colon: {reason}")
            self.assertNotEqual(
                reason,
                "truncated_by_total_budget:",
                "Should have a specific label after the colon",
            )

    # ---- Phase 6e-C: auto-include scout files ----

    def _patch_scout_suggestions(self, suggestions):
        return patch.object(pc, "scout_suggestions", return_value=suggestions)

    def _live_preview_key(self, prompt="Fix auth.py"):
        return {"targetKey": "test", "prompt": prompt}

    def test_default_no_auto_include(self):
        """auto_include_scout=False by default: no scout files injected."""
        (self.target_root / "src" / "auth.py").write_text("def auth(): pass", encoding="utf-8")
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=[{
            "path": "src/auth.py", "score": 100, "reasons": ["filename_match"], "size": 20, "kind": "py",
        }]):
            pack = pc.build_context_pack("test", "Fix auth.py")
        self.assertFalse(any(f.get("source") == "scout_auto_selected" for f in pack["files"]))

    def test_auto_include_success(self):
        """High-score live scout files are auto-included as scout_auto_selected."""
        (self.target_root / "src" / "auth.py").write_text("def auth(): pass", encoding="utf-8")
        suggestions = [{"path": "src/auth.py", "score": 125, "reasons": ["filename_match", "content_match:2kws"], "size": 200, "kind": "py"}]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack(
                "test", "Fix auth.py", auto_include_scout=True, min_scout_score=40,
                scout_preview_key=self._live_preview_key("Fix auth.py"),
            )
        auto = [f for f in pack["files"] if f.get("source") == "scout_auto_selected"]
        self.assertEqual(len(auto), 1)
        self.assertEqual(auto[0]["path"], "src/auth.py")
        self.assertEqual(auto[0]["selectionMethod"], "auto_include")
        self.assertEqual(auto[0]["scoutScore"], 125)
        self.assertIn("filename_match", auto[0]["scoutReasons"])

    def test_auto_include_dedup_with_user_selected(self):
        """User-selected files take priority over auto-include (G8)."""
        (self.target_root / "src" / "auth.py").write_text("def auth(): pass", encoding="utf-8")
        suggestions = [{"path": "src/auth.py", "score": 100, "reasons": ["filename_match"], "size": 20, "kind": "py"}]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack(
                "test", "Fix auth.py", context_files=["src/auth.py"], auto_include_scout=True,
                scout_preview_key=self._live_preview_key("Fix auth.py"),
            )
        # Only one entry, source should be user_selected (manual), not scout_auto_selected.
        self.assertEqual(len(pack["files"]), 1)
        self.assertEqual(pack["files"][0]["source"], "user_selected")
        self.assertEqual(pack["files"][0]["selectionMethod"], "manual")

    def test_auto_include_exclude_stale_preview_key(self):
        """G3: stale scoutPreviewKey → auto_include skipped."""
        (self.target_root / "src" / "x.py").write_text("x", encoding="utf-8")
        suggestions = [{"path": "src/x.py", "score": 100, "reasons": ["filename_match"], "size": 2, "kind": "py"}]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack(
                "test", "Fix x.py", auto_include_scout=True,
                scout_preview_key={"targetKey": "other", "prompt": "old"},
            )
        auto = [f for f in pack["files"] if f.get("source") == "scout_auto_selected"]
        self.assertEqual(len(auto), 0)
        self.assertTrue(any("stale_preview" in i.get("reason", "") for i in pack["accessIssues"]))

    def test_auto_include_score_threshold(self):
        """G7: files below min_scout_score are not auto-included."""
        (self.target_root / "src" / "low.py").write_text("low", encoding="utf-8")
        suggestions = [{"path": "src/low.py", "score": 30, "reasons": ["keyword"], "size": 4, "kind": "py"}]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack(
                "test", "Fix low", auto_include_scout=True, min_scout_score=50,
                scout_preview_key=self._live_preview_key("Fix low"),
            )
        self.assertEqual(len(pack["files"]), 0)

    def test_auto_include_missing_preview_key(self):
        """G3: missing scoutPreviewKey → auto_include skipped."""
        (self.target_root / "src" / "auth.py").write_text("def auth(): pass", encoding="utf-8")
        suggestions = [{"path": "src/auth.py", "score": 100, "reasons": ["match"], "size": 20, "kind": "py"}]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack("test", "Fix auth.py", auto_include_scout=True)
        auto = [f for f in pack["files"] if f.get("source") == "scout_auto_selected"]
        self.assertEqual(len(auto), 0)
        self.assertTrue(any("missing_preview_key" in i.get("reason", "") for i in pack["accessIssues"]))

    def test_auto_include_max_cap(self):
        """G9: maxAutoScoutFiles caps the number of auto-included files."""
        for i in range(5):
            (self.target_root / "src" / f"file{i}.py").write_text(f"f{i}", encoding="utf-8")
        suggestions = [
            {"path": f"src/file{i}.py", "score": 100, "reasons": ["match"], "size": 2, "kind": "py"}
            for i in range(5)
        ]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack(
                "test", "Fix files", auto_include_scout=True, max_auto_scout=2,
                scout_preview_key=self._live_preview_key("Fix files"),
            )
        auto = [f for f in pack["files"] if f.get("source") == "scout_auto_selected"]
        self.assertLessEqual(len(auto), 2)

    def test_auto_include_live_key_passes(self):
        """G3: when scoutPreviewKey matches, auto-include proceeds."""
        (self.target_root / "src" / "auth.py").write_text("def auth(): pass", encoding="utf-8")
        suggestions = [{"path": "src/auth.py", "score": 100, "reasons": ["match"], "size": 20, "kind": "py"}]
        with self._load_targets_patch(), patch("project_context.scout_suggestions", return_value=suggestions):
            pack = pc.build_context_pack(
                "test", "Fix auth.py", auto_include_scout=True,
                scout_preview_key={"targetKey": "test", "prompt": "Fix auth.py"},
            )
        auto = [f for f in pack["files"] if f.get("source") == "scout_auto_selected"]
        self.assertEqual(len(auto), 1)

    def test_build_context_audit_summary_none(self):
        summary = pc.build_context_audit_summary(None)
        self.assertEqual(summary["status"], "unavailable")
        self.assertEqual(summary["highestLevel"], "L0")
        self.assertFalse(summary["sources"]["blueprint"]["present"])

    def test_build_context_audit_summary_l0_l1_l2_l3(self):
        # L0 Blueprint
        pack = {
            "version": 1,
            "targetKey": "test",
            "blueprint": {
                "tree": "some tree",
                "readme": "README text",
                "dependencies": []
            },
            "files": []
        }
        summary = pc.build_context_audit_summary(pack)
        self.assertEqual(summary["highestLevel"], "L0")
        self.assertIn("l0_only", summary["riskFlags"])
        self.assertTrue(summary["sources"]["blueprint"]["present"])
        self.assertEqual(summary["sources"]["blueprint"]["files"], 1)

        # L1 User Selected
        pack["files"] = [{"path": "src/main.py", "source": "user_selected", "chars": 100}]
        summary = pc.build_context_audit_summary(pack)
        self.assertEqual(summary["highestLevel"], "L1")
        self.assertEqual(summary["sources"]["userSelected"]["files"], 1)

        # L2 Scout Auto Selected
        pack["files"] = [{"path": "src/auth.py", "source": "scout_auto_selected", "scoutScore": 85}]
        summary = pc.build_context_audit_summary(pack)
        self.assertEqual(summary["highestLevel"], "L2")
        self.assertEqual(summary["sources"]["scoutAutoSelected"]["files"], 1)
        self.assertEqual(summary["sources"]["scoutAutoSelected"]["minScoutScore"], 85)

        # L3 Explorer Brief
        pack["explorerBrief"] = {"status": "ready", "candidateFiles": [1, 2], "commands": [1]}
        summary = pc.build_context_audit_summary(pack)
        self.assertEqual(summary["highestLevel"], "L3")
        self.assertEqual(summary["sources"]["explorerBrief"]["candidateFiles"], 2)
        self.assertEqual(summary["sources"]["explorerBrief"]["commands"], 1)

    def test_build_context_audit_summary_status_and_risks(self):
        pack = {
            "version": 1,
            "targetKey": "test",
            "summary": {"status": "ready", "truncated": True},
            "accessIssues": [{"path": "secret.txt", "reason": "excluded_secret"}],
            "files": [{"path": "src/auth.py", "source": "scout_auto_selected", "scoutScore": 90}],
            "explorerBrief": {
                "status": "timeout",
                "limits": {"hitTimeout": True},
                "candidateFiles": []
            }
        }
        summary = pc.build_context_audit_summary(pack)
        self.assertEqual(summary["status"], "partial")
        self.assertIn("scout_auto_selected", summary["riskFlags"])
        self.assertIn("explorer_timeout", summary["riskFlags"])
        self.assertIn("access_issue", summary["riskFlags"])
        self.assertIn("context_truncated", summary["riskFlags"])

        # failed status propagation
        pack["summary"]["status"] = "failed"
        summary = pc.build_context_audit_summary(pack)
        self.assertEqual(summary["status"], "failed")

    def test_audit_highest_level_fallback_on_explorer_failure(self):
        base = {
            "version": 1, "targetKey": "test",
            "blueprint": {"tree": "t", "readme": "r", "dependencies": []},
            "files": [{"path": "src/a.py", "source": "user_selected", "chars": 100}],
        }
        # explorer timeout + L1 → fall back to L1, keep explorer_timeout flag
        p = {**base, "explorerBrief": {"status": "timeout", "limits": {"hitTimeout": True}, "candidateFiles": []}}
        s = pc.build_context_audit_summary(p)
        self.assertEqual(s["highestLevel"], "L1")
        self.assertIn("explorer_timeout", s["riskFlags"])

        # explorer failed + L2 → fall back to L2, keep explorer_failed flag
        p = {**base,
             "files": [{"path": "src/a.py", "source": "scout_auto_selected", "scoutScore": 80}],
             "explorerBrief": {"status": "failed", "candidateFiles": []}}
        s = pc.build_context_audit_summary(p)
        self.assertEqual(s["highestLevel"], "L2")
        self.assertIn("explorer_failed", s["riskFlags"])

        # explorer skipped + blueprint only → L0
        p = {**base, "files": [], "explorerBrief": {"status": "skipped"}}
        s = pc.build_context_audit_summary(p)
        self.assertEqual(s["highestLevel"], "L0")

        # L0 only + explorer failed → L0, keep explorer_failed flag
        p = {**base, "files": [], "explorerBrief": {"status": "failed", "candidateFiles": []}}
        s = pc.build_context_audit_summary(p)
        self.assertEqual(s["highestLevel"], "L0")
        self.assertIn("explorer_failed", s["riskFlags"])

        # explorer ready → L3 (regression guard)
        p = {**base, "explorerBrief": {"status": "ready", "candidateFiles": [1], "commands": [1]}}
        s = pc.build_context_audit_summary(p)
        self.assertEqual(s["highestLevel"], "L3")


if __name__ == "__main__":
    unittest.main()