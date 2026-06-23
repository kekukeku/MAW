"""Target project context gathering for the MAW Context-Aware Council.

Phase 6a scope:
- L0 Project Blueprint (tree, README, dependency files).
- Read-only, path-safe, secret-denylisted.

Phase 6d-A scope:
- L1 user-selected context files with full security validation.
- list_safe_files() for the file browser API.
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
from scout import scout_suggestions

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


def _validate_context_file_path(rel_path: str, root: Path) -> Path:
    """Validate and resolve a user-selected context file path.

    Returns the resolved absolute Path if valid.
    Raises ContextTargetError on any violation.
    """
    if not rel_path or not rel_path.strip():
        raise ContextTargetError("Empty file path")

    rel_path = rel_path.strip()

    if os.path.isabs(rel_path):
        raise ContextTargetError(f"Rejected absolute path: {rel_path}")

    if ".." in Path(rel_path).parts:
        raise ContextTargetError(f"Rejected traversal path: {rel_path}")

    raw = root / rel_path

    try:
        resolved = raw.resolve()
    except (OSError, RuntimeError) as e:
        raise ContextTargetError(f"Cannot resolve path {rel_path}: {e}")

    root_resolved = root.resolve()
    if not (resolved == root_resolved or root_resolved in resolved.parents):
        raise ContextTargetError(f"Path escapes target root: {rel_path}")

    if not resolved.is_file():
        raise ContextTargetError(f"Not a regular file: {rel_path}")

    return resolved


def _read_l1_file(
    absolute_path: Path, rel_path: str, root: Path, policy: dict[str, Any]
) -> dict[str, Any]:
    """Read a single L1 user-selected file with safety and budget checks.

    Returns a file record dict.
    Raises ContextTargetError for hard policy violations.
    """
    max_file_chars = policy.get("maxFileChars", 12000)

    excluded, reason = _is_always_excluded(absolute_path, root)
    if excluded:
        raise ContextTargetError(f"Excluded file ({reason}): {rel_path}")

    # Check gitignore.
    if policy.get("respectGitignore", True):
        ignored = _batch_git_ignored([absolute_path], root)
        if absolute_path in ignored:
            raise ContextTargetError(f"Gitignored file rejected: {rel_path}")

    try:
        with open(absolute_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ContextTargetError(f"Cannot read file {rel_path}: {e}")

    if b"\x00" in raw:
        raise ContextTargetError(f"Binary file rejected: {rel_path}")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            raise ContextTargetError(f"Unreadable encoding: {rel_path}")

    content = text
    truncated = False
    if len(content) > max_file_chars:
        head = max_file_chars // 2
        tail = max_file_chars - head
        content = (
            text[:head]
            + f"\n\n[... {len(text) - max_file_chars} chars omitted from {rel_path} ...]\n\n"
            + text[-tail:]
        )
        truncated = True

    return {
        "path": rel_path,
        "source": "user_selected",
        "reason": "User selected",
        "chars": len(content),
        "truncated": truncated,
        "sha256": _sha256_text(content),
        "content": content,
    }


def list_safe_files(target_key: str) -> list[dict[str, Any]]:
    """Return a sanitised list of files available for user selection.

    Used by GET /api/maw/targets/{targetKey}/files.
    Returns metadata only — no file contents.
    """
    targets = load_targets()
    projects = targets.get("projects", {})
    if target_key not in projects:
        raise ContextTargetError(f"Unknown target key: '{target_key}'.")

    target_path = projects[target_key].get("path", "")
    root = Path(get_project_root(target_path))
    if not root.is_dir():
        raise ContextTargetError(f"Target directory does not exist: {root}")

    candidates, _issues = _collect_candidate_files(root, DEFAULT_POLICY)
    result: list[dict[str, Any]] = []
    for f in candidates:
        rel = f.relative_to(root).as_posix()
        try:
            st = f.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            size = 0
            mtime = None

        suffix = f.suffix.lower().lstrip(".")
        kind = suffix if suffix else "unknown"

        is_binary = False
        try:
            with open(f, "rb") as fh:
                peek = fh.read(512)
            if b"\x00" in peek:
                is_binary = True
        except OSError:
            is_binary = True

        if is_binary:
            continue

        result.append({
            "path": rel,
            "size": size,
            "kind": kind,
            "mtime": mtime,
        })

    result.sort(key=lambda x: (not x["path"].startswith(("src/", "lib/", "app/")), x["path"].lower()))
    return result


def build_context_pack(
    target_key: str,
    prompt: str,
    context_files: list[str] | None = None,
    auto_scout: bool = False,
    policy: dict[str, Any] | None = None,
    auto_include_scout: bool = False,
    max_auto_scout: int = 3,
    min_scout_score: int = 40,
    scout_preview_key: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a context pack for the given target project.

    L0: Project Blueprint (tree, README, dependency files) always produced.
    L1: If context_files is non-empty, user-selected files are read and added.
    Auto-include: if auto_include_scout=True, top-scored scout suggestions
    are added as scout_auto_selected after passing G1-G9 safety gates.

    Raises:
        ContextTargetError: if target_key is unknown or target path is invalid.
    """
    context_files = context_files or []
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

    # --- L1 user-selected context files ---
    l1_files: list[dict[str, Any]] = []
    for rel_path in context_files:
        try:
            abs_path = _validate_context_file_path(rel_path, root)
            file_record = _read_l1_file(abs_path, rel_path, root, effective_policy)
            file_record["selectionMethod"] = "manual"
            l1_files.append(file_record)
        except ContextTargetError as e:
            access_issues.append({"path": rel_path, "reason": f"l1_rejected: {e}"})

    # --- L2 scout auto-include (G1-G9) ---
    auto_files: list[dict[str, Any]] = []
    user_selected_paths = {f["path"] for f in l1_files}
    if auto_include_scout and prompt.strip():
        # G3: require scoutPreviewKey and verify it matches current request.
        if not scout_preview_key:
            preview_key_ok = False
            skip_reason = "scout_auto_skipped:missing_preview_key"
        else:
            preview_key_ok = (
                scout_preview_key.get("targetKey") == target_key
                and scout_preview_key.get("prompt") == prompt
            )
            skip_reason = "scout_auto_skipped:stale_preview"
        if not preview_key_ok:
            access_issues.append({
                "path": "<scout_auto>",
                "reason": skip_reason,
            })
        else:
            try:
                suggestions = scout_suggestions(target_key, prompt, max_results=max_auto_scout * 3)
            except Exception:
                access_issues.append({
                    "path": "<scout_auto>",
                    "reason": "scout_auto_skipped:suggestions_failed",
                })
                suggestions = []

            safe_paths = {f["path"] for f in suggestions}
            added = 0
            for sug in suggestions:
                if added >= max_auto_scout:
                    access_issues.append({
                        "path": "<scout_auto>",
                        "reason": f"scout_auto_truncated:max_{max_auto_scout}",
                    })
                    break

                # G7: score threshold.
                if sug["score"] < min_scout_score:
                    continue

                # G5: file must be in safe file list (already enforced by scout_suggestions).
                # G6: path safety + not secret/binary/gitignored.
                try:
                    abs_path = _validate_context_file_path(sug["path"], root)
                    file_record = _read_l1_file(abs_path, sug["path"], root, effective_policy)
                except ContextTargetError as e:
                    access_issues.append({"path": sug["path"], "reason": f"scout_auto_rejected: {e}"})
                    continue

                # G8: dedup — user-selected takes priority.
                if sug["path"] in user_selected_paths:
                    continue

                file_record["source"] = "scout_auto_selected"
                file_record["reason"] = "Scout auto-selected"
                file_record["selectionMethod"] = "auto_include"
                file_record["scoutScore"] = sug["score"]
                file_record["scoutReasons"] = sug.get("reasons", [])
                auto_files.append(file_record)
                added += 1

    all_l1 = l1_files + auto_files

    # Compute included file count and truncation.
    included_files = 0
    if readme_record:
        included_files += 1
    included_files += len(dependency_records)
    included_files += len(all_l1)

    total_chars = len(tree) + (len(readme_text) if readme_text else 0)
    for rec in dependency_records:
        total_chars += len(rec.get("content", ""))
    for rec in all_l1:
        total_chars += len(rec.get("content", ""))

    truncated = any(
        issue.get("reason", "").startswith("truncated") for issue in access_issues
    )
    for rec in all_l1:
        if rec.get("truncated"):
            truncated = True

    blueprint: dict[str, Any] = {
        "tree": tree,
        "readme": readme_text or "",
    }
    blueprint["dependencies"] = dependency_records

    context_level = "L1" if all_l1 else "L0"

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
        "level": context_level,
        "targetPath": str(root),
        "generatedAt": generated_at,
        "policy": effective_policy,
        "summary": summary,
        "blueprint": blueprint,
        "files": all_l1,
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

    Budget priority ordering (highest first):
      1. Context Status (small, always fits)
      2. Selected / Scout Files (L1 — never truncated by total budget)
      3. Project Blueprint (tree, README, dependencies — cut from lowest priority)
      4. Context Boundaries + User Request (always last)

    Truncation markers are appended to context_pack["accessIssues"] for provenance.
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

    policy = context_pack.get("policy", {})
    max_total = policy.get("maxTotalChars", 50000)
    summary = context_pack.get("summary", {})
    blueprint = context_pack.get("blueprint", {})

    # ---- P1: Context Status ----
    status_lines = [
        "# Target Project Context",
        "",
        "## Context Status",
        f"- Context pack version: {context_pack.get('version')}",
        f"- Target project: {context_pack.get('targetKey')}",
        f"- Included files: {summary.get('includedFiles', 0)}",
        f"- Total chars: {summary.get('totalChars', 0)}",
        f"- Truncated: {summary.get('truncated', False)}",
        "",
    ]
    status_str = "\n".join(status_lines)

    # ---- P2: Selected / Scout Files (highest priority) ----
    l1_str = ""
    files = context_pack.get("files", [])
    user_files = [f for f in files if f.get("source") != "scout_auto_selected"]
    auto_files_selected = [f for f in files if f.get("source") == "scout_auto_selected"]

    if user_files:
        l1_parts = ["## Selected / Scout Files", "", "### User-Selected Files", ""]
        for f in user_files:
            path = f.get("path", "")
            source = f.get("source", "")
            content = f.get("content", "")
            sel_method = f.get("selectionMethod", "")
            method_tag = f" (via {sel_method})" if sel_method else ""
            l1_parts.extend([
                f"#### File: {path} (source: {source}{method_tag})",
                "```text",
                content,
                "```",
                "",
            ])
        if auto_files_selected:
            l1_parts.extend(["### Scout Auto-Selected Files", ""])
            for f in auto_files_selected:
                path = f.get("path", "")
                score = f.get("scoutScore", 0)
                reasons = ", ".join(f.get("scoutReasons", []))
                content = f.get("content", "")
                l1_parts.extend([
                    f"#### File: {path} (source: scout_auto_selected, score: {score})",
                    f"- Reasons: {reasons}",
                    "- Instruction: This file was automatically included by the system. Refer to it for context, but do not change it unless requested.",
                    "```text",
                    content,
                    "```",
                    "",
                ])
        l1_str = "\n".join(l1_parts)
    elif auto_files_selected:
        l1_parts = ["## Selected / Scout Files", "", "### Scout Auto-Selected Files", ""]
        for f in auto_files_selected:
            path = f.get("path", "")
            score = f.get("scoutScore", 0)
            reasons = ", ".join(f.get("scoutReasons", []))
            content = f.get("content", "")
            l1_parts.extend([
                f"#### File: {path} (source: scout_auto_selected, score: {score})",
                f"- Reasons: {reasons}",
                "- Instruction: This file was automatically included by the system.",
                "```text",
                content,
                "```",
                "",
            ])
        l1_str = "\n".join(l1_parts)

    # ---- P2.5: Explorer Research Brief (L3) ----
    explorer_str = ""
    explorer_brief = context_pack.get("explorerBrief")
    if explorer_brief and explorer_brief.get("status") in ("ready", "partial"):
        exp_lines = [
            "## Explorer Research Brief (L3 — NOT source of truth)",
            "- This section is an automated read-only research summary.",
            "- Prefer User-Selected and Scout Auto-Selected file contents over this brief.",
            "- Files listed with contentIncluded=false were NOT fully read.",
            "",
        ]
        summary = explorer_brief.get("summary", "")
        if summary:
            exp_lines.extend(["### Summary", summary, ""])
        candidate_files = explorer_brief.get("candidateFiles", [])
        if candidate_files:
            exp_lines.append("### Candidate Files Metadata")
            for cf in candidate_files:
                path = cf.get("path", "")
                excerpt = cf.get("excerpt", "")
                truncated = " (truncated)" if cf.get("truncated") else ""
                unread = " [content not read]" if cf.get("contentIncluded") is False else ""
                exp_lines.append(f"- {path}{truncated}{unread}:")
                if excerpt:
                    exp_lines.append(f"  - Excerpt: `{excerpt[:200]}`")
            exp_lines.append("")
        explorer_str = "\n".join(exp_lines)

    # ---- P4: Context Boundaries + User Request (always last) ----
    boundary_str = "\n".join([
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
    ])

    # Compute remaining budget for L0 blueprint.
    fixed_chars = len(status_str) + len(l1_str) + len(explorer_str) + len(boundary_str)
    remaining = max(0, max_total - fixed_chars)
    truncated_parts: list[str] = []

    # ---- P3: Blueprint within remaining budget ----
    bp_parts = ["## Project Blueprint", ""]

    # 3a. Tree (lowest priority: cut first)
    tree = blueprint.get("tree", "")
    if tree:
        if remaining > 0:
            tree_header = "### Directory Tree\n```text\n"
            tree_footer = "\n```\n"
            tree_avail = remaining - len(tree_header) - len(tree_footer)
            if tree_avail > 0:
                if len(tree) <= tree_avail:
                    bp_parts.append(f"{tree_header}{tree}{tree_footer}")
                    remaining -= len(tree_header) + len(tree) + len(tree_footer)
                else:
                    cut = tree[:max(0, tree_avail - 60)]
                    omit_msg = f"\n[... tree truncated by total budget: {len(tree) - len(cut)} chars omitted ...]\n"
                    bp_parts.append(f"{tree_header}{cut}{omit_msg}{tree_footer}")
                    remaining = 0
                    truncated_parts.append(f"tree_truncated:{len(tree)}chars")
            else:
                bp_parts.append("### Directory Tree\n[... tree omitted — insufficient budget ...]\n\n")
                remaining = max(0, remaining - len(tree_header) - len(tree_footer))
                truncated_parts.append("tree_omitted_no_budget")
        else:
            bp_parts.append("### Directory Tree\n[... tree omitted — no budget remaining ...]\n\n")
            truncated_parts.append("tree_omitted_no_budget")

    # 3b. README (medium priority)
    readme = blueprint.get("readme", "")
    if readme and remaining > 100:
        readme_header = "### README\n"
        avail = remaining - len(readme_header)
        if avail > 100:
            if len(readme) <= avail:
                bp_parts.extend([readme_header, readme, ""])
                remaining -= len(readme_header) + len(readme) + 1
            else:
                cut = readme[:max(0, avail - 60)]
                omit_msg = f"\n[... README truncated by total budget: {len(readme) - len(cut)} chars omitted ...]\n"
                bp_parts.extend([readme_header, cut, omit_msg, ""])
                remaining = 0
                truncated_parts.append(f"readme_truncated:{len(readme)}chars")
        else:
            bp_parts.append(f"### README\n[... omitted — insufficient budget ({remaining} chars) ...]\n\n")
            remaining = 0
            truncated_parts.append("readme_omitted_no_budget")

    # 3c. Dependencies (highest L0 priority within blueprint)
    deps = blueprint.get("dependencies", [])
    if deps and remaining > 0:
        dep_header = "### Dependencies / Config Files\n"
        bp_parts.append(dep_header)
        remaining -= len(dep_header)
        for dep in deps:
            if remaining <= 50:
                truncated_parts.append("dependencies_partial")
                break
            path = dep.get("path", "")
            content = dep.get("content", "")
            truncated_marker = " (truncated)" if dep.get("truncated") else ""
            dep_title = f"#### {path}{truncated_marker}\n```text\n"
            dep_footer = "\n```\n"
            dep_overhead = len(dep_title) + len(dep_footer)
            avail = remaining - dep_overhead
            if avail > 50:
                if len(content) <= avail:
                    bp_parts.append(f"{dep_title}{content}{dep_footer}")
                    remaining -= dep_overhead + len(content)
                else:
                    cut = content[:max(0, avail - 60)]
                    omit_msg = f"\n[... dependency file truncated by total budget ...]\n"
                    bp_parts.append(f"{dep_title}{cut}{omit_msg}{dep_footer}")
                    remaining = 0
                    truncated_parts.append(f"dependency_truncated:{path}")
            else:
                truncated_parts.append(f"dependency_omitted_no_budget:{path}")

    # Assemble in priority order.
    result = status_str + "\n" + l1_str + "\n" + explorer_str + "\n" + "\n".join(bp_parts) + "\n" + boundary_str

    # Record truncation markers in accessIssues for provenance.
    if truncated_parts:
        context_pack.setdefault("accessIssues", [])
        for part in truncated_parts:
            # Deduplicate.
            if not any(
                i.get("reason", "") == f"truncated_by_total_budget:{part}"
                for i in context_pack["accessIssues"]
            ):
                context_pack["accessIssues"].append({
                    "path": "<prompt_envelope>",
                    "reason": f"truncated_by_total_budget:{part}",
                })

    return result


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


def build_context_preview_response(
    context_pack: dict[str, Any],
    suggested_files: list[dict[str, Any]] | None = None,
    would_auto_include: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a slim, UI-safe preview of a context pack.

    Removes heavy text blobs while keeping enough metadata for the
    Panel 1 context bar and preview modal.  When suggested_files is
    provided (scout results), they are included in the response without
    being auto-injected into context_pack.files.
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

    result: dict[str, Any] = {
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
    if suggested_files:
        result["suggestedFiles"] = [
            {
                "path": s["path"],
                "score": s["score"],
                "reasons": s.get("reasons", []),
                "size": s.get("size", 0),
                "kind": s.get("kind", "unknown"),
            }
            for s in suggested_files
        ]
    if "explorerBrief" in context_pack:
        result["explorerBrief"] = context_pack["explorerBrief"]
    if would_auto_include is not None:
        result["wouldAutoInclude"] = would_auto_include
    return result


def build_context_audit_summary(context_pack: dict[str, Any] | None) -> dict[str, Any]:
    """Build a standard, structured audit summary of the target project context.

    This is the SINGLE SOURCE OF TRUTH for context audits, risk flags, and status.
    """
    if context_pack is None:
        return {
            "contextPackVersion": 1,
            "targetKey": "",
            "status": "unavailable",
            "highestLevel": "L0",
            "sources": {
                "blueprint": {
                    "present": False,
                    "files": 0,
                    "truncated": False
                },
                "userSelected": {
                    "files": 0,
                    "chars": 0,
                    "paths": []
                },
                "scoutAutoSelected": {
                    "files": 0,
                    "minScoutScore": 0,
                    "paths": []
                },
                "explorerBrief": {
                    "present": False,
                    "status": None,
                    "candidateFiles": 0,
                    "commands": 0,
                    "hitTimeout": False
                }
            },
            "riskFlags": [],
            "accessIssueCount": 0,
            "promptIncluded": {
                "blueprint": False,
                "userSelectedFiles": False,
                "scoutAutoSelectedFiles": False,
                "explorerBrief": False
            }
        }

    version = context_pack.get("version", CONTEXT_PACK_VERSION)
    target_key = context_pack.get("targetKey", "")
    access_issues = context_pack.get("accessIssues", [])

    # 1. status state machine
    pack_status = context_pack.get("status") or context_pack.get("summary", {}).get("status") or "ready"
    if pack_status == "failed":
        status = "failed"
    elif access_issues:
        status = "partial"
    else:
        status = "ready"

    # 2. files parsing
    files = context_pack.get("files", [])
    user_files = [f for f in files if f.get("source") == "user_selected"]
    scout_files = [f for f in files if f.get("source") == "scout_auto_selected"]

    # 3. explorerBrief parsing
    eb = context_pack.get("explorerBrief")
    eb_present = eb is not None
    eb_status = eb.get("status") if eb else None
    eb_candidate_files = len(eb.get("candidateFiles", [])) if eb else 0
    eb_commands = len(eb.get("commands", [])) if eb else 0
    eb_hit_timeout = False
    if eb:
        eb_hit_timeout = eb.get("status") == "timeout" or eb.get("limits", {}).get("hitTimeout", False)

    # 4. highestLevel logic
    # Phase 6g.1: explorer brief only counts as L3 when it actually produced
    # usable research (ready/partial). timeout/failed/skipped fall back to the
    # level implied by scout/user/blueprint, but risk flags are preserved.
    explorer_usable = eb_present and eb_status in ("ready", "partial")
    if explorer_usable:
        highest_level = "L3"
    elif scout_files:
        highest_level = "L2"
    elif user_files:
        highest_level = "L1"
    else:
        highest_level = "L0"

    # 5. blueprint details
    bp = context_pack.get("blueprint", {})
    bp_present = bool(bp)
    bp_files = 0
    bp_truncated = False
    if bp:
        if bp.get("readme"):
            bp_files += 1
        deps = bp.get("dependencies", [])
        bp_files += len(deps)
        bp_truncated = any(dep.get("truncated") for dep in deps)

    # 6. userSelected details
    user_chars = sum(f.get("chars", len(f.get("content", ""))) for f in user_files)
    user_paths = [f.get("path") for f in user_files]

    # 7. scoutAutoSelected details
    scout_min_score = min(f.get("scoutScore", 0) for f in scout_files) if scout_files else 0
    scout_paths = [f.get("path") for f in scout_files]

    # 8. riskFlags
    risk_flags = []
    if scout_files:
        risk_flags.append("scout_auto_selected")
    if eb_hit_timeout:
        risk_flags.append("explorer_timeout")
    if eb_status == "failed":
        risk_flags.append("explorer_failed")
    if access_issues:
        risk_flags.append("access_issue")
    if context_pack.get("summary", {}).get("truncated", False):
        risk_flags.append("context_truncated")
    if highest_level == "L0":
        risk_flags.append("l0_only")

    # 9. promptIncluded
    prompt_included = {
        "blueprint": bool(bp.get("tree")),
        "userSelectedFiles": len(user_files) > 0,
        "scoutAutoSelectedFiles": len(scout_files) > 0,
        "explorerBrief": eb_present and eb_status in ("ready", "partial"),
    }

    return {
        "contextPackVersion": version,
        "targetKey": target_key,
        "status": status,
        "highestLevel": highest_level,
        "sources": {
            "blueprint": {
                "present": bp_present,
                "files": bp_files,
                "truncated": bp_truncated
            },
            "userSelected": {
                "files": len(user_files),
                "chars": user_chars,
                "paths": user_paths
            },
            "scoutAutoSelected": {
                "files": len(scout_files),
                "minScoutScore": scout_min_score,
                "paths": scout_paths
            },
            "explorerBrief": {
                "present": eb_present,
                "status": eb_status,
                "candidateFiles": eb_candidate_files,
                "commands": eb_commands,
                "hitTimeout": eb_hit_timeout
            }
        },
        "riskFlags": risk_flags,
        "accessIssueCount": len(access_issues),
        "promptIncluded": prompt_included
    }
