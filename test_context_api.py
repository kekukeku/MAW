import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
import project_context as pc
from project_context import ContextTargetError, build_context_preview_response


class TestContextPreviewAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.test_dir = Path(tempfile.mkdtemp())
        self.target_root = self.test_dir / "target"
        self.target_root.mkdir()
        (self.target_root / "README.md").write_text("# Preview API Test\n", encoding="utf-8")
        (self.target_root / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        subprocess.run(["git", "init", "-b", "main"], cwd=self.target_root, capture_output=True, check=False)
        self.targets = {
            "default": "test",
            "projects": {
                "test": {"name": "Test Target", "path": str(self.target_root)},
            },
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_preview_context_success_slim_shape(self):
        with patch.object(pc, "load_targets", return_value=self.targets):
            res = self.client.post(
                "/api/maw/context/preview",
                json={"targetKey": "test", "prompt": "Implement feature X"},
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["targetKey"], "test")
        self.assertEqual(body["level"], "L0")
        self.assertIn("blueprint", body)
        self.assertNotIn("targetPath", body)
        self.assertNotIn("content", str(body.get("blueprint", {})))
        self.assertIn("l0_only", body.get("warnings", []))

    def test_preview_context_target_error(self):
        with patch.object(
            main, "build_context_pack", side_effect=ContextTargetError("unknown target")
        ):
            res = self.client.post(
                "/api/maw/context/preview",
                json={"targetKey": "missing", "prompt": "Implement feature X"},
            )
        self.assertEqual(res.status_code, 400)
        self.assertIn("unknown target", res.json()["detail"])

    def test_preview_context_internal_error(self):
        with patch.object(
            main, "build_context_pack", side_effect=RuntimeError("disk full")
        ):
            res = self.client.post(
                "/api/maw/context/preview",
                json={"targetKey": "test", "prompt": "Implement feature X"},
            )
        self.assertEqual(res.status_code, 500)
        self.assertIn("disk full", res.json()["detail"])

    def test_preview_context_passes_context_files(self):
        """Phase 6d-A: preview API forwards contextFiles to build_context_pack."""
        with patch.object(main, "build_context_pack", return_value={
            "version": 1, "targetKey": "test", "level": "L1", "summary": {"includedFiles": 1, "totalChars": 10, "truncated": False},
            "blueprint": {"tree": "test", "readme": "", "dependencies": []}, "files": [{"path": "src/a.py", "source": "user_selected"}],
            "accessIssues": [],
        }) as mock_build, patch.object(main, "build_context_preview_response", return_value={
            "version": 1, "target_key": "test", "level": "L1", "files": [{"path": "src/a.py", "source": "user_selected"}],
            "total_tokens": 4, "warnings": [],
        }):
            res = self.client.post(
                "/api/maw/context/preview",
                json={
                    "targetKey": "test",
                    "prompt": "Implement feature X",
                    "contextFiles": ["src/a.py"],
                    "autoScoutContext": False,
                },
            )
        self.assertEqual(res.status_code, 200)
        mock_build.assert_called_once_with(
            target_key="test",
            prompt="Implement feature X",
            context_files=["src/a.py"],
            auto_scout=False,
        )

    def test_build_context_preview_response_warnings(self):
        pack = {
            "version": 1,
            "targetKey": "test",
            "level": "L0",
            "summary": {"status": "ready", "includedFiles": 2, "totalChars": 1000, "truncated": True},
            "blueprint": {"tree": "a\nb", "readme": "hi", "dependencies": [{"path": "package.json", "content": "SECRET"}]},
            "files": [],
            "accessIssues": [{"path": ".env", "reason": "excluded_secret:.env"}],
        }
        preview = build_context_preview_response(pack)
        self.assertEqual(preview["warnings"], ["l0_only", "truncated"])
        self.assertNotIn("targetPath", preview)
        self.assertNotIn("content", preview["blueprint"])


    def test_list_target_files_api(self):
        with patch.object(pc, "load_targets", return_value=self.targets):
            res = self.client.get("/api/maw/targets/test/files")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIsInstance(body, list)
        for entry in body:
            self.assertIn("path", entry)
            self.assertIn("size", entry)
            self.assertIn("kind", entry)
            self.assertIn("mtime", entry)
        paths = {e["path"] for e in body}
        self.assertIn("README.md", paths)
        self.assertIn("package.json", paths)

    def test_list_target_files_unknown_target(self):
        with patch.object(main, "list_safe_files", side_effect=ContextTargetError("unknown target")):
            res = self.client.get("/api/maw/targets/bogus/files")
        self.assertEqual(res.status_code, 400)

    def test_list_target_files_internal_error(self):
        with patch.object(main, "list_safe_files", side_effect=RuntimeError("disk error")):
            res = self.client.get("/api/maw/targets/test/files")
        self.assertEqual(res.status_code, 500)

    def test_preview_includes_suggested_files_when_scout_enabled(self):
        suggestions = [
            {"path": "src/auth.py", "score": 100, "reasons": ["filename_match"], "size": 200, "kind": "py"},
            {"path": "src/tokens.py", "score": 60, "reasons": ["keyword_in_path:token"], "size": 100, "kind": "py"},
        ]
        preview = {"version": 1, "targetKey": "test", "level": "L0", "summary": {"includedFiles": 0, "totalChars": 0, "truncated": False}, "total_tokens": 0,
                    "files": [], "blueprint": {"hasReadme": False, "dependencyPaths": [], "treePreview": "", "treeTruncated": False},
                    "accessIssues": [], "totalAccessIssues": 0, "warnings": ["l0_only"]}
        with patch.object(main, "build_context_pack", return_value={
            "version": 1, "targetKey": "test", "level": "L0",
            "summary": {"includedFiles": 0, "totalChars": 0, "truncated": False},
            "blueprint": {"tree": "", "readme": "", "dependencies": []},
            "files": [], "accessIssues": [],
        }), patch.object(main, "scout_suggestions", return_value=suggestions), \
           patch.object(main, "build_context_preview_response", side_effect=lambda cp, **kw: preview):
            res = self.client.post(
                "/api/maw/context/preview",
                json={"targetKey": "test", "prompt": "Fix authentication", "autoScoutContext": True},
            )
        self.assertEqual(res.status_code, 200)

    def test_preview_no_suggested_files_when_scout_disabled(self):
        """When autoScoutContext=False, suggestedFiles is absent from preview."""
        with patch.object(main, "build_context_pack", return_value={
            "version": 1, "targetKey": "test", "level": "L0",
            "summary": {"includedFiles": 0, "totalChars": 0, "truncated": False},
            "blueprint": {"tree": "", "readme": "", "dependencies": []},
            "files": [], "accessIssues": [],
        }), patch.object(main, "build_context_preview_response", return_value={"targetKey": "test", "level": "L0"}):
            res = self.client.post(
                "/api/maw/context/preview",
                json={"targetKey": "test", "prompt": "Fix auth", "autoScoutContext": False},
            )
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("suggestedFiles", res.json())


if __name__ == "__main__":
    unittest.main()