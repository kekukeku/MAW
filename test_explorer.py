import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import explorer as ex
import project_context as pc


class TestExplorer(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.target_root = self.test_dir / "target"
        self.target_root.mkdir()
        (self.target_root / "README.md").write_text("# Explorer Test\n", encoding="utf-8")
        (self.target_root / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        src_dir = self.target_root / "src"
        src_dir.mkdir()
        auth_dir = src_dir / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "authentication.py").write_text(
            "class TokenManager:\n    def __init__(self):\n        self.expire_in = 3600\n"
            "    def check_token(self, token):\n        if token.expired:\n            raise TokenExpiryError()\n",
            encoding="utf-8",
        )
        (auth_dir / "session_manager.py").write_text(
            "def create_session():\n    pass\n", encoding="utf-8",
        )
        (src_dir / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        tests_dir = self.target_root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_auth.py").write_text("def test_auth():\n    pass\n", encoding="utf-8")

        # Files that should NOT appear.
        (self.target_root / ".env").write_text("SECRET=12345", encoding="utf-8")
        (self.target_root / "node_modules").mkdir()
        (self.target_root / "node_modules" / "lib.js").write_text("var x=1", encoding="utf-8")
        (self.target_root / "MAW_workflow").mkdir()
        (self.target_root / "MAW_workflow" / "state.md").write_text("state", encoding="utf-8")
        (self.target_root / ".git").mkdir()

        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.target_root, capture_output=True, check=False,
        )

        self.targets = {
            "default": "test",
            "projects": {"test": {"name": "Test", "path": str(self.target_root)}},
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _patch_load_targets(self):
        return patch.object(ex, "load_targets", return_value=self.targets)

    # --- Core execution tests ---

    def test_explorer_returns_schema(self):
        with self._patch_load_targets(), patch.object(ex, "scout_suggestions", return_value=[
            {"path": "src/auth/authentication.py", "score": 100, "reasons": ["filename_match"], "size": 200, "kind": "py"},
        ]), patch.object(ex, "_detect_rg", return_value=False):
            brief = ex.run_explorer_brief("test", "Fix token expiry", max_files_read=3)
        self.assertIn("version", brief)
        self.assertIn("candidateFiles", brief)
        self.assertIn("relevantAreas", brief)
        self.assertIn("commands", brief)
        self.assertIn("limits", brief)

    def test_explorer_candidate_files_have_excerpt(self):
        with self._patch_load_targets(), patch.object(ex, "scout_suggestions", return_value=[
            {"path": "src/auth/authentication.py", "score": 100, "reasons": ["match"], "size": 200, "kind": "py"},
        ]), patch.object(ex, "_detect_rg", return_value=False):
            brief = ex.run_explorer_brief("test", "Fix token expiry", max_files_read=3)
        self.assertGreater(len(brief["candidateFiles"]), 0)
        for cf in brief["candidateFiles"]:
            self.assertIn("excerpt", cf)

    # --- Timeout test ---

    def test_explorer_timeout(self):
        with self._patch_load_targets(), patch.object(ex, "scout_suggestions", return_value=[
            {"path": "src/auth/authentication.py", "score": 100, "reasons": ["match"], "size": 200, "kind": "py"},
        ]):
            # Patch _explorer_core to sleep forever → triggers timeout.
            def _slow_core(*args, **kwargs):
                import time; time.sleep(99)
            with patch.object(ex, "_explorer_core", side_effect=_slow_core):
                brief = ex.run_explorer_brief("test", "Fix token", timeout_seconds=1)
        self.assertEqual(brief["status"], "timeout")
        self.assertTrue(brief["limits"]["hitTimeout"])
        self.assertEqual(brief["summary"], "")

    # --- Empty prompt ---

    def test_explorer_empty_prompt_skips(self):
        brief = ex.run_explorer_brief("test", "")
        self.assertEqual(brief["status"], "skipped")
        self.assertTrue(any("empty_prompt" in i.get("reason", "") for i in brief["accessIssues"]))

    # --- Secrets masking ---

    def test_mask_secret_path(self):
        self.assertEqual(ex._mask_secret_path(".env"), "[secret_env_masked]")
        self.assertEqual(ex._mask_secret_path("config/prod_secrets.key"), "config/[secret_key_masked]")
        self.assertEqual(ex._mask_secret_path(".env.production"), "[secret_env_masked]")
        self.assertEqual(ex._mask_secret_path("certs/server.pem"), "certs/[secret_pem_masked]")
        self.assertEqual(ex._mask_secret_path("some/cred.xyz"), "some/[secret_file_masked]")

    # --- Excluded dirs/files not searched ---

    def test_explorer_excludes_secrets_via_always_excluded(self):
        fp = self.target_root / ".env"
        excluded, reason = pc._is_always_excluded(fp, self.target_root)
        self.assertTrue(excluded)

    # --- Performance: defensive read at I/O level ---

    def test_defensive_read_limits_io(self):
        big = self.target_root / "big.py"
        big_content = "x" * 20000
        big.write_text(big_content, encoding="utf-8")
        result = ex._defensive_read(big, max_chars=500)
        self.assertLessEqual(len(result), 500)

    def test_defensive_read_rejects_binary(self):
        binary = self.target_root / "data.bin"
        binary.write_bytes(b"\x00\x01\x02\x03")
        result = ex._defensive_read(binary, max_chars=100)
        self.assertEqual(result, "")

    # --- rg detection ---

    def test_detect_rg(self):
        # rg is installed in most dev environments; at minimum the function runs.
        result = ex._detect_rg()
        self.assertIsInstance(result, bool)

    # --- Scope-limited search ---

    def test_get_search_scope_derives_from_scout(self):
        with self._patch_load_targets(), patch.object(ex, "scout_suggestions", return_value=[
            {"path": "src/auth/authentication.py", "score": 100, "reasons": ["match"], "size": 200, "kind": "py"},
        ]):
            dirs, hits = ex._get_search_scope("test", "Fix token", self.target_root)
        self.assertGreater(len(dirs), 0)
        auth_parent = self.target_root / "src" / "auth"
        self.assertTrue(any(str(d) == str(auth_parent) for d in dirs))

    # --- LLM summary not implemented ---

    def test_allow_llm_summary_reserved(self):
        """allow_llm_summary is accepted but not implemented in Phase 6f-A."""
        with self._patch_load_targets(), patch.object(ex, "scout_suggestions", return_value=[
            {"path": "src/auth/authentication.py", "score": 100, "reasons": ["match"], "size": 200, "kind": "py"},
        ]), patch.object(ex, "_detect_rg", return_value=False):
            brief = ex.run_explorer_brief("test", "Fix token", allow_llm_summary=True)
        self.assertIn("summary", brief)

    def test_python_search_skips_secret_files(self):
        (self.target_root / "src" / "leak.py").write_text("token expiry secret", encoding="utf-8")
        lines, _ = ex._search_with_python(
            "token expiry",
            [self.target_root],
            self.target_root,
            max_results=20,
        )
        paths = {line.split(":", 1)[0] for line in lines}
        self.assertNotIn(".env", paths)

    def test_is_safe_search_file_rejects_env(self):
        env_path = self.target_root / ".env"
        self.assertFalse(ex._is_safe_search_file(env_path, self.target_root, pc.DEFAULT_POLICY))


if __name__ == "__main__":
    unittest.main()
