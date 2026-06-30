import os
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PATHS = [
    "council",
    "main.py",
    "loop_orchestrator.py",
    "setup_api.py",
    "export.py",
    "project_context.py",
    "scout.py",
    "explorer.py",
    "maw_paths.py",
    "adapters",
    "template_target_project",
]

REQUIRED_PATHS = [
    "v2/dispatcher.py",
    "v2/adapters/__init__.py",
    "v2_templates/AGENTS.md",
    "v2_templates/TEAM_RULES.md",
]

FORBIDDEN_TOKENS = [
    "LLM_PROVIDER",
    "LITELLM_API_KEY",
    "LITELLM_API_BASE",
    "OPENROUTER_API_KEY",
    "DEFAULT_COUNCIL_MODELS",
    "DEFAULT_CHAIRMAN_MODEL",
    "uvicorn main:app",
    "loop_orchestrator",
]

ACTIVE_SURFACES = [
    "README.md",
    ".env.example",
    "MAW.command",
    "install.command",
    "start.sh",
    "pyproject.toml",
    "v2",
    "v2_templates",
]

ALLOWED_LOCATIONS = {
    "docs/archive-v1",
}


class CutoverTest(unittest.TestCase):
    def test_a_forbidden_v1_paths_do_not_exist(self):
        missing = []
        for p in FORBIDDEN_PATHS:
            if (REPO_ROOT / p).exists():
                missing.append(p)
        self.assertEqual([], missing, f"Forbidden v1 paths still exist: {missing}")

    def test_b_required_v2_paths_exist(self):
        missing = []
        for p in REQUIRED_PATHS:
            if not (REPO_ROOT / p).exists():
                missing.append(p)
        self.assertEqual([], missing, f"Required v2 paths missing: {missing}")

    def test_c_forbidden_v1_tokens_absent_from_active_surfaces(self):
        violations = []
        for surface_path in ACTIVE_SURFACES:
            surface = REPO_ROOT / surface_path
            if not surface.exists():
                continue
            self._scan(surface, str(surface_path), violations)
        self.assertEqual([], violations, f"Forbidden v1 tokens found:\n" + "\n".join(violations))

    def _scan(self, path, rel, violations):
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    # Skip allowed locations
                    rel_child = str(child.relative_to(REPO_ROOT))
                    if any(rel_child.startswith(loc) for loc in ALLOWED_LOCATIONS):
                        continue
                    # Only scan text-ish files
                    if child.suffix in (".py", ".md", ".toml", ".sh", ".command"):
                        self._check_file(child, rel_child, violations)
        else:
            self._check_file(path, rel, violations)

    def _check_file(self, path, rel, violations):
        try:
            content = path.read_text()
        except Exception:
            return
        for token in FORBIDDEN_TOKENS:
            if token in content:
                violations.append(f"  {rel}: contains '{token}'")
