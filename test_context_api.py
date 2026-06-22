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

    def test_preview_context_ignores_context_files_and_auto_scout(self):
        """Phase 6c-A accepts but ignores contextFiles and autoScoutContext."""
        with patch.object(pc, "load_targets", return_value=self.targets), patch.object(
            main, "build_context_pack", wraps=pc.build_context_pack
        ) as mock_build:
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
        mock_build.assert_called_once_with(target_key="test", prompt="Implement feature X")

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


if __name__ == "__main__":
    unittest.main()