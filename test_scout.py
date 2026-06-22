import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scout
from project_context import DEFAULT_POLICY
import project_context as pc


class TestScout(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.target_root = self.test_dir / "target"
        self.target_root.mkdir()
        (self.target_root / "README.md").write_text("# Test\n", encoding="utf-8")
        (self.target_root / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        src_dir = self.target_root / "src"
        src_dir.mkdir()
        auth_dir = src_dir / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "authentication.py").write_text(
            "def authenticate(token):\n    if token.expired:\n        raise TokenExpiryError()\n",
            encoding="utf-8",
        )
        (auth_dir / "tokens.py").write_text(
            "def refresh_token():\n    pass\n", encoding="utf-8",
        )
        (src_dir / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        tests_dir = self.target_root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_authentication.py").write_text("def test_auth():\n    pass\n", encoding="utf-8")

        # Files that should NOT appear in scout results.
        (self.target_root / ".env").write_text("SECRET=123", encoding="utf-8")
        (self.target_root / "node_modules").mkdir()
        (self.target_root / "node_modules" / "lib.js").write_text("var x=1", encoding="utf-8")
        (self.target_root / "MAW_workflow").mkdir()
        (self.target_root / "MAW_workflow" / "state.md").write_text("state", encoding="utf-8")
        (self.target_root / ".git").mkdir()

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

    def _patch_load_targets(self):
        return patch.object(pc, "load_targets", return_value=self.targets)

    def test_filename_match_scores_high(self):
        """Prompt mentioning a filename should rank it top."""
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "Fix the authentication.py file")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["path"], "src/auth/authentication.py")
        self.assertGreater(results[0]["score"], 90)

    def test_keyword_content_match(self):
        """Prompt mentioning 'token expiry' should match auth content."""
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "Handle token expiry")
        paths = {r["path"] for r in results}
        self.assertTrue(
            "src/auth/authentication.py" in paths or "src/auth/tokens.py" in paths,
            f"Expected auth files in results: {paths}",
        )

    def test_secret_files_never_suggested(self):
        """Secret files (.env) must not appear in scout results."""
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "SECRET=123")
        paths = {r["path"] for r in results}
        self.assertNotIn(".env", paths)

    def test_build_dir_files_never_suggested(self):
        """node_modules files must not appear in scout results."""
        (self.target_root / "node_modules" / "helper.js").write_text("helper", encoding="utf-8")
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "helper.js")
        for r in results:
            self.assertNotIn("node_modules", r["path"])

    def test_workflow_dir_files_never_suggested(self):
        """MAW_workflow files must not appear in scout results."""
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "state.md")
        for r in results:
            self.assertNotIn("MAW_workflow", r["path"])

    def test_suggestions_not_auto_injected_into_context_pack(self):
        """Scout results are standalone; they never enter context_pack.files."""
        with self._patch_load_targets():
            suggestions = scout.scout_suggestions("test", "authentication.py")
        # Scout doesn't modify context_pack — it just returns suggestions.
        self.assertIsInstance(suggestions, list)
        for s in suggestions:
            self.assertIn("path", s)
            self.assertIn("score", s)
            self.assertIn("reasons", s)

    def test_test_file_bonus(self):
        """Files with a nearby test file get a bonus."""
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "authentication.py")
        auth_result = next((r for r in results if r["path"] == "src/auth/authentication.py"), None)
        self.assertIsNotNone(auth_result)
        self.assertIn("test_file_nearby", auth_result["reasons"])

    def test_empty_prompt_returns_empty(self):
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "")
        self.assertEqual(results, [])

    def test_suggestions_have_required_keys(self):
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "authentication test")
        for r in results:
            for key in ("path", "score", "reasons", "size", "kind"):
                self.assertIn(key, r, f"Missing key '{key}' in {r}")

    def test_max_results_limit(self):
        with self._patch_load_targets():
            results = scout.scout_suggestions("test", "authentication", max_results=2)
        self.assertLessEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
