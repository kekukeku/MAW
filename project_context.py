"""Target project context gathering for the MAW Context-Aware Council.

Phase 6a scope:
- L0 Project Blueprint only (tree, README, dependency files).
- No L1/L2/L3 context sources.
- No UI, no preview API.
- Read-only, path-safe, secret-denylisted.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from export import load_targets
from maw_paths import get_project_root

logger = logging.getLogger(__name__)


class ContextTargetError(ValueError):
    """Raised when a target key cannot be resolved or the target path is invalid."""

    pass


CONTEXT_PACK_VERSION = 1

# Default character budgets for Phase 6a.
DEFAULT_POLICY: dict[str, Any] = {
    "respectGitignore": True,
    "excludeSecrets": True,
    "excludeWorkflowDir": True,
    "maxTotalChars": 50000,
    "maxFileChars": 12000,
    "maxTreeChars": 10000,
    "maxReadmeChars": 4000,
    "maxDependencyFileChars": 6000,
    "maxTreeEntries": 200,
    "maxScoutFiles": 0,
}

# Directories/files always excluded from context.
ALWAYS_EXCLUDED_DIRS = {
    ".git",
    "MAW_workflow",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "coverage",
    ".next",
    ".turbo",
}

ALWAYS_EXCLUDED_PATTERNS = [
    "*.pyc",
    "*.log",
    "*.sqlite",
    "*.db",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.pdf",
]

# Secret/sensitive filename patterns.
SECRET_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.crt",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account*.json",
]

# Dependency/config files to include in L0 blueprint.
DEPENDENCY_FILES = [
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
]

README_CANDIDATES = [
    "README.md",
    "README.rst",
    "README",
    "readme.md",
]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _is_always_excluded(path: Path, root: Path) -> tuple[bool, str | None]:
    """Return (excluded, reason) for always-excluded paths."""
    rel = path.relative_to(root)
    parts = rel.parts

    for part in parts:
        if part in ALWAYS_EXCLUDED_DIRS:
            return True, f"excluded_dir:{part}"
        if _matches_any(part, ALWAYS_EXCLUDED_PATTERNS):
            return True, f"excluded_pattern:{part}"
        if _matches_any(part, SECRET_PATTERNS):
            return True, f"excluded_secret:{part}"

    return False, None


def _is_safe_within_root(path: Path, root: Path) -> bool:
    """Verify that path resolves to a location inside root (no traversal, no symlink escape)."""
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        return resolved == root_resolved or root_resolved in resolved.parents
    except (OSError, RuntimeError):
        return False


def _batch_git_ignored(paths: list[Path], root: Path) -> set[Path]:
    """Use `git check-ignore --stdin` to batch-check ignored paths.

    Returns the subset of `paths` that git considers ignored.
    Falls back to an empty set if git is unavailable or fails.
    """
    if not paths:
        return set()

    git_dir = root / ".git"
    if not git_dir.is_dir():
        return set()

    rel_paths = []
    path_by_rel: dict[str, Path] = {}
    for p in paths:
        try:
            rel = p.relative_to(root).as_posix()
            rel_paths.append(rel)
            path_by_rel[rel] = p
        except ValueError:
            continue

    if not rel_paths:
        return set()

    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(rel_paths) + "\n",
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        logger.warning("git check-ignore failed or unavailable; falling back to denylist")
        return set()

    ignored: set[Path] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line in path_by_rel:
            ignored.add(path_by_rel[line])
    return ignored


def _safe_read_text(path: Path, max_chars: int | None = None) -> str:
    """Read text from path with optional truncation.

    Returns empty string for binary files or read errors.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""

    # Simple binary detection: if null bytes present, treat as binary.
    if b"\x00" in raw:
        return ""

    # Try UTF-8 first, then latin-1 as fallback.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            return ""

    if max_chars is not None and len(text) > max_chars:
        head = max_chars // 2
        tail = max_chars - head
        return text[:head] + f"\n\n[... {len(text) - max_chars} chars omitted ...]\n\n" + text[-tail:]
    return text


def _build_directory_tree(root: Path, policy: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Generate a directory tree string and collect access issues.

    Returns (tree_string, access_issues).
    """
    max_entries = policy.get("maxTreeEntries", 200)
    max_chars = policy.get("maxTreeChars", 10000)

    lines: list[str] = []
    issues: list[dict[str, Any]] = []
    entry_count = 0

    def _walk(current: Path, prefix: str = "") -> None:
        nonlocal entry_count
        try:
            entries = sorted(
                [e for e in current.iterdir() if e.name != ".DS_Store"],
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            rel = current.relative_to(root).as_posix() if current != root else "."
            issues.append({"path": rel, "reason": "permission_denied"})
            return
        except OSError:
            return

        for index, entry in enumerate(entries):
            if entry_count >= max_entries:
                lines.append(f"{prefix}... ({max_entries} tree entries limit reached)")
                return

            excluded, reason = _is_always_excluded(entry, root)
            if excluded:
                continue

            is_last = index == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            entry_count += 1

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension)

    lines.append(root.name or str(root))
    _walk(root)

    tree = "\n".join(lines)
    if len(tree) > max_chars:
        head = max_chars // 2
        tail = max_chars - head
        tree = tree[:head] + f"\n\n[... {len(tree) - max_chars} tree chars omitted ...]\n\n" + tree[-tail:]
        issues.append({"path": "<tree>", "reason": "truncated_by_size"})

    return tree, issues


def _apply_gitignore_filter(paths: list[Path], root: Path, policy: dict[str, Any]) -> tuple[list[Path], list[dict[str, Any]]]:
    """Filter paths using git check-ignore when available.

    Returns (filtered_paths, access_issues).
    """
    if not policy.get("respectGitignore", True):
        return paths, []

    issues: list[dict[str, Any]] = []

    if (root / ".git").is_dir():
        try:
            ignored = _batch_git_ignored(paths, root)
            filtered = [p for p in paths if p not in ignored]
            for p in ignored:
                rel = p.relative_to(root).as_posix()
                issues.append({"path": rel, "reason": "excluded_by_gitignore"})
            return filtered, issues
        except Exception as exc:
            logger.warning("gitignore filtering failed: %s; using denylist only", exc)

    return paths, []


def _collect_candidate_files(root: Path, policy: dict[str, Any]) -> tuple[list[Path], list[dict[str, Any]]]:
    """Collect all non-excluded file paths under root.

    Returns (candidate_files, access_issues).
    """
    candidates: list[Path] = []
    issues: list[dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        current = Path(dirpath)

        # Prune always-excluded directories.
        to_prune = []
        for d in list(dirnames):
            dir_full = current / d
            excluded, reason = _is_always_excluded(dir_full, root)
            if excluded:
                to_prune.append(d)
                rel = dir_full.relative_to(root).as_posix()
                issues.append({"path": rel, "reason": reason})
        for d in to_prune:
            dirnames.remove(d)

        for filename in filenames:
            file_path = current / filename
            excluded, reason = _is_always_excluded(file_path, root)
            if excluded:
                rel = file_path.relative_to(root).as_posix()
                issues.append({"path": rel, "reason": reason})
                continue
            candidates.append(file_path)

    candidates, gitignore_issues = _apply_gitignore_filter(candidates, root, policy)
    issues.extend(gitignore_issues)
    return candidates, issues


def _read_readme(root: Path, candidates: list[Path], policy: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Find and read the README file.

    Returns (content, file_record).
    """
    max_chars = policy.get("maxReadmeChars", 4000)

    for name in README_CANDIDATES:
        path = root / name
        if path.is_file() and path in candidates:
            text = _safe_read_text(path, max_chars=max_chars)
            rel = path.relative_to(root).as_posix()
            truncated = len(text) >= max_chars and "omitted" in text
            record = {
                "path": rel,
                "source": "blueprint",
                "reason": "README",
                "chars": len(text),
                "truncated": truncated,
                "sha256": _sha256_text(text),
            }
            return text, record

    return None, None


def _read_dependency_files(
    root: Path,
    candidates: list[Path],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read dependency/config files and summarize test/lint scripts.

    Returns (dependency_records, access_issues).
    """
    max_chars = policy.get("maxDependencyFileChars", 6000)
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for dep_name in DEPENDENCY_FILES:
        path = root / dep_name
        if not path.is_file() or path not in candidates:
            continue

        text = _safe_read_text(path, max_chars=max_chars)
        if not text:
            continue

        rel = path.relative_to(root).as_posix()
        truncated = len(text) >= max_chars and "omitted" in text

        # Extract a lightweight scripts summary when possible.
        scripts_summary = _extract_scripts_summary(dep_name, text)
        content = text
        if scripts_summary:
            content = f"{scripts_summary}\n\n---\n\n{text}"
            # Re-truncate if scripts summary pushed us over budget.
            if len(content) > max_chars:
                content = content[:max_chars]
                truncated = True

        records.append({
            "path": rel,
            "source": "blueprint",
            "reason": "dependency_file",
            "chars": len(content),
            "truncated": truncated,
            "sha256": _sha256_text(content),
            "content": content,
        })

    return records, issues


def _extract_scripts_summary(dep_name: str, text: str) -> str | None:
    """Extract test/lint script names from common config files."""
    if dep_name == "package.json":
        import json
        try:
            data = json.loads(text)
            scripts = data.get("scripts", {})
            relevant = {k: v for k, v in scripts.items() if any(tok in k.lower() for tok in ("test", "lint", "format", "build"))}
            if relevant:
                return "### package.json scripts\n" + "\n".join(f"- {k}: {v}" for k, v in relevant.items())
        except Exception:
            pass
    elif dep_name in ("pyproject.toml",):
        # Very light parser: look for [tool.pytest.ini_options] or test-related sections.
        lines = text.splitlines()
        summary_lines: list[str] = []
        in_scripts = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[project.scripts]"):
                in_scripts = True
                summary_lines.append("### project scripts")
                continue
            if stripped.startswith("[") and in_scripts:
                break
            if in_scripts and "=" in stripped:
                summary_lines.append(f"- {stripped}")
        if summary_lines:
            return "\n".join(summary_lines)
    return None


def build_context_pack(
    target_key: str,
    prompt: str,
    context_files: list[str] | None = None,
    auto_scout: bool = False,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an L0 Project Blueprint context pack for the given target project.

    Args:
        target_key: key into MAW targets.json.
        prompt: the original user request (used for provenance, not for Scout in Phase 6a).
        context_files: Phase 6a ignores this (reserved for L1 in Phase 6c+).
        auto_scout: Phase 6a ignores this (reserved for L2 in Phase 6e+).
        policy: optional override for context budget/policy.

    Returns:
        A context pack dict matching the schema in CONTEXT_AWARE_COUNCIL_REFACTOR_PLAN.md.

    Raises:
        ValueError: if target_key is unknown or target path is invalid.
    """
    context_files = context_files or []
    auto_scout = False  # Force off in Phase 6a.
    effective_policy = {**DEFAULT_POLICY, **(policy or {})}

    targets = load_targets()
    projects = targets.get("projects", {})
    if target_key not in projects:
        raise ContextTargetError(f"Unknown target key: '{target_key}'.")

    target_path = projects[target_key].get("path", "")
    root = Path(get_project_root(target_path))
    if not root.is_dir():
        raise ContextTargetError(f"Target directory does not exist: {root}")

    generated_at = datetime.now(timezone.utc).isoformat()
    access_issues: list[dict[str, Any]] = []

    # Collect candidate files and apply exclusions.
    candidate_files, collect_issues = _collect_candidate_files(root, effective_policy)
    access_issues.extend(collect_issues)

    # Build directory tree.
    tree, tree_issues = _build_directory_tree(root, effective_policy)
    access_issues.extend(tree_issues)

    # Read README.
    readme_text, readme_record = _read_readme(root, candidate_files, effective_policy)

    # Read dependency files.
    dependency_records, dep_issues = _read_dependency_files(root, candidate_files, effective_policy)
    access_issues.extend(dep_issues)

    # Compute included file count and truncation.
    included_files = 0
    if readme_record:
        included_files += 1
    included_files += len(dependency_records)

    total_chars = len(tree) + (len(readme_text) if readme_text else 0)
    for rec in dependency_records:
        total_chars += len(rec.get("content", ""))

    truncated = any(
        issue.get("reason", "").startswith("truncated") for issue in access_issues
    )

    blueprint: dict[str, Any] = {
        "tree": tree,
        "readme": readme_text or "",
    }
    blueprint["dependencies"] = dependency_records

    summary = {
        "status": "ready",
        "totalChars": total_chars,
        "truncated": truncated,
        "includedFiles": included_files,
        "excludedFiles": len(access_issues),
    }

    context_pack: dict[str, Any] = {
        "version": CONTEXT_PACK_VERSION,
        "targetKey": target_key,
        "level": "L0",
        "targetPath": str(root),
        "generatedAt": generated_at,
        "policy": effective_policy,
        "summary": summary,
        "blueprint": blueprint,
        "files": [],  # L1/L2 placeholder for Phase 6c+.
        "accessIssues": access_issues,
    }

    # Defensive budget check: if total chars exceed maxTotalChars, mark truncated.
    max_total = effective_policy.get("maxTotalChars", 50000)
    if total_chars > max_total:
        context_pack["summary"]["truncated"] = True
        context_pack["accessIssues"].append({
            "path": "<context_pack>",
            "reason": f"exceeded_total_budget:{total_chars}/{max_total}",
        })

    return context_pack


def build_prompt_envelope(prompt: str, context_pack: dict[str, Any] | None) -> str:
    """Build the full context-aware prompt envelope.

    If context_pack is None, return the prompt with an explicit unavailable marker.
    """
    if context_pack is None:
        return (
            "# Target Project Context\n\n"
            "## Context Status\n"
            "- Context pack version: unavailable\n"
            "- Context status: unavailable\n"
            "- Warning: this council was invoked without target project context.\n\n"
            "## Context Boundaries\n"
            "- No project files were provided.\n"
            "- Make only generic recommendations unless you have other information.\n\n"
            "# User Request\n\n"
            f"{prompt}"
        )

    summary = context_pack.get("summary", {})
    blueprint = context_pack.get("blueprint", {})

    context_lines: list[str] = [
        "# Target Project Context",
        "",
        "## Context Status",
        f"- Context pack version: {context_pack.get('version')}",
        f"- Target project: {context_pack.get('targetKey')}",
        f"- Included files: {summary.get('includedFiles', 0)}",
        f"- Total chars: {summary.get('totalChars', 0)}",
        f"- Truncated: {summary.get('truncated', False)}",
        "",
        "## Project Blueprint",
        "",
        "### Directory Tree",
        "```text",
        blueprint.get("tree", ""),
        "```",
        "",
    ]

    readme = blueprint.get("readme", "")
    if readme:
        context_lines.extend([
            "### README",
            readme,
            "",
        ])

    deps = blueprint.get("dependencies", [])
    if deps:
        context_lines.append("### Dependencies / Config Files")
        for dep in deps:
            path = dep.get("path", "")
            content = dep.get("content", "")
            truncated_marker = " (truncated)" if dep.get("truncated") else ""
            context_lines.extend([
                f"#### {path}{truncated_marker}",
                "```text",
                content,
                "```",
                "",
            ])

    files = context_pack.get("files", [])
    if files:
        context_lines.extend([
            "## Selected / Scout Files",
            "",
        ])
        for f in files:
            path = f.get("path", "")
            source = f.get("source", "")
            content = f.get("content", "")
            context_lines.extend([
                f"### File: {path} (source: {source})",
                "```text",
                content,
                "```",
                "",
            ])

    context_str = "\n".join(context_lines)
    max_total = context_pack.get("policy", {}).get("maxTotalChars", 50000)
    if len(context_str) > max_total:
        context_str = context_str[:max_total] + "\n\n[... Context truncated due to character budget limit ...]\n\n"

    boundary_lines = [
        "## Context Boundaries",
        "- You may only make concrete claims based on the provided context.",
        "- If the context is insufficient, explicitly list the missing files or information.",
        "- Do not assume unseen implementation details.",
        "- Produce a plan that names files/functions only when supported by context.",
        "- If only the project blueprint (tree/README/dependencies) is available, you may plan at the project level but must not invent specific function bodies or file contents.",
        "",
        "# User Request",
        "",
        prompt,
    ]

    return context_str + "\n" + "\n".join(boundary_lines)


def compact_context_digest(context_pack: dict[str, Any]) -> str:
    """Return a compact digest for Stage 2 ranking prompt.

    This is shorter than the full envelope but still context-aware.
    """
    summary = context_pack.get("summary", {})
    blueprint = context_pack.get("blueprint", {})
    lines = [
        "## Target Project Context (compact digest)",
        f"- Target project: {context_pack.get('targetKey')}",
        f"- Included files: {summary.get('includedFiles', 0)}",
        f"- Total chars: {summary.get('totalChars', 0)}",
        f"- Directory tree (first 20 lines):",
    ]
    tree_lines = blueprint.get("tree", "").splitlines()[:20]
    lines.extend(tree_lines)
    deps = blueprint.get("dependencies", [])
    if deps:
        dep_names = ", ".join(d.get("path", "") for d in deps)
        lines.append(f"- Dependency/config files: {dep_names}")
    lines.append("- Context boundary: only make claims supported by the provided project context.")
    return "\n".join(lines)


def build_context_preview_response(context_pack: dict[str, Any]) -> dict[str, Any]:
    """Return a slim, UI-safe preview of a context pack.

    Removes heavy text blobs (README text, dependency file contents, full tree)
    while keeping enough metadata for the Panel 1 context bar and preview modal.
    """
    summary = context_pack.get("summary", {})
    blueprint = context_pack.get("blueprint", {})
    files = context_pack.get("files", [])
    access_issues = context_pack.get("accessIssues", [])

    warnings_list: list[str] = []
    if not files:
        warnings_list.append("l0_only")
    if summary.get("truncated"):
        warnings_list.append("truncated")

    tree_text = blueprint.get("tree", "")
    tree_lines = tree_text.splitlines()
    tree_preview = "\n".join(tree_lines[:30])
    tree_truncated = len(tree_lines) > 30

    total_chars = summary.get("totalChars", 0)
    total_tokens = int(total_chars / 4) if total_chars else 0

    return {
        "version": context_pack.get("version", CONTEXT_PACK_VERSION),
        "targetKey": context_pack.get("targetKey", ""),
        "level": context_pack.get("level", "L0"),
        "status": summary.get("status", "unknown"),
        "summary": {
            "includedFiles": summary.get("includedFiles", 0),
            "totalChars": total_chars,
            "truncated": summary.get("truncated", False),
            "excludedFiles": len(access_issues),
        },
        "total_tokens": total_tokens,
        "files": [
            {
                "path": f.get("path", ""),
                "source": f.get("source", ""),
                "chars": f.get("chars", 0),
                "truncated": f.get("truncated", False),
            }
            for f in files
        ],
        "blueprint": {
            "hasReadme": bool(blueprint.get("readme")),
            "dependencyPaths": [d.get("path", "") for d in blueprint.get("dependencies", [])],
            "treePreview": tree_preview,
            "treeTruncated": tree_truncated,
        },
        "accessIssues": access_issues[:20],
        "totalAccessIssues": len(access_issues),
        "warnings": warnings_list,
    }
