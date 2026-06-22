"""L3 Explorer: read-only project research layer.

Phase 6f-A: produces an ExplorerBrief for Council context.
Never modifies files.  Read-only, target-root-confined, timeout-guarded.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from export import load_targets
from maw_paths import get_project_root
from project_context import (
    ContextTargetError,
    DEFAULT_POLICY,
    _is_always_excluded,
    _batch_git_ignored,
)
from scout import scout_suggestions

logger = logging.getLogger(__name__)

# Default limits for Phase 6f.
DEFAULT_MAX_FILES_READ = 8
DEFAULT_MAX_CHARS_READ = 24000
DEFAULT_TIMEOUT_SECONDS = 15

# Secret patterns for path masking.
_SECRET_MASK_PATTERNS = [
    (re.compile(r"\.env(\..*)?"), "[secret_env_masked]"),
    (re.compile(r".*\.pem"), "[secret_pem_masked]"),
    (re.compile(r".*\.key"), "[secret_key_masked]"),
    (re.compile(r".*\.p12"), "[secret_p12_masked]"),
    (re.compile(r".*\.crt"), "[secret_crt_masked]"),
    (re.compile(r".*id_rsa.*"), "[secret_ssh_key_masked]"),
    (re.compile(r".*credentials.*\.json"), "[secret_credentials_masked]"),
]

# Keywords that look like test directories.
_TEST_DIR_KEYWORDS = {"tests", "test", "__tests__", "spec", "__spec__"}


def _mask_secret_path(rel_path: str) -> str:
    """Mask a secret file path to prevent sensitive structure leakage."""
    parts = rel_path.replace("\\", "/").split("/")
    filename = parts[-1]

    # Try to mask the filename with known secret patterns.
    for pattern, replacement in _SECRET_MASK_PATTERNS:
        if pattern.match(filename):
            parts[-1] = replacement
            return "/".join(parts)

    # Fallback: keep directory, mask filename generically.
    if len(parts) <= 1:
        return "[secret_file_masked]"
    parts[-1] = "[secret_file_masked]"
    return "/".join(parts)


def _detect_rg() -> bool:
    """Check if ripgrep is available on the system."""
    return shutil.which("rg") is not None


def _search_with_rg(
    query: str,
    directories: list[Path],
    root: Path,
    max_results: int = 50,
    timeout: int = 10,
) -> tuple[list[str], float]:
    """Search using ripgrep across given directories.

    Returns (matching_lines, duration_ms).
    """
    if not directories:
        return [], 0.0

    dir_strs = [str(d) for d in directories]
    t0 = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            [
                "rg",
                "--no-heading",
                "--with-filename",
                "--line-number",
                "--max-count=50",
                "--max-filesize=500K",
                "-i",
                query,
                *dir_strs,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    except Exception:
        return [], 0.0

    duration = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    lines = proc.stdout.strip().splitlines()
    return lines[:max_results], duration


def _search_with_python(
    query: str,
    directories: list[Path],
    root: Path,
    max_results: int = 50,
) -> tuple[list[str], float]:
    """Search using Python os.walk + re.search (fallback when rg unavailable)."""
    if not directories:
        return [], 0.0

    t0 = datetime.now(timezone.utc)
    results: list[str] = []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    seen_files: set[str] = set()

    for directory in directories:
        if len(results) >= max_results:
            break
        if not directory.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(directory):
            if len(results) >= max_results:
                break
            for filename in filenames:
                if len(results) >= max_results:
                    break
                fp = Path(dirpath) / filename
                rel = fp.relative_to(root).as_posix()
                if rel in seen_files:
                    continue
                seen_files.add(rel)
                try:
                    with open(fp, "rb") as f:
                        raw = f.read(50000)
                except OSError:
                    continue
                if b"\x00" in raw:
                    continue
                try:
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    continue
                for li, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        results.append(f"{rel}:{li}:{line[:200]}")
                        if len(results) >= max_results:
                            break

    duration = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    return results[:max_results], duration


def _get_search_scope(
    target_key: str,
    prompt: str,
    root: Path,
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Derive search directories from scout suggestions.

    Returns (search_dirs, scout_hits_info).
    """
    try:
        suggestions = scout_suggestions(target_key, prompt, max_results=5)
    except Exception:
        return [], []

    dirs: dict[str, Path] = {}
    hits: list[dict[str, Any]] = []
    for sug in suggestions:
        path = sug["path"]
        hits.append({"path": path, "score": sug["score"], "reasons": sug.get("reasons", [])})
        # Add the directory containing the matched file.
        parent = (root / path).parent
        if parent.is_dir():
            dirs[str(parent)] = parent
        # Also add neighboring test directories.
        rel_parent = path.rsplit("/", 1)[0] if "/" in path else ""
        for td in _TEST_DIR_KEYWORDS:
            test_dir = root / td
            if test_dir.is_dir():
                dirs[str(test_dir)] = test_dir
            if rel_parent:
                test_nested = root / td / rel_parent
                if test_nested.is_dir():
                    dirs[str(test_nested)] = test_nested

    return list(dirs.values()), hits


def _defensive_read(path: Path, max_chars: int) -> str:
    """Read at most max_chars from a file with binary/encoding safety.

    Never reads more than max_chars from disk (I/O-level limit).
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(max_chars)
    except OSError:
        return ""

    if b"\x00" in raw:
        return ""

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            return ""


def run_explorer_brief(
    target_key: str,
    prompt: str,
    max_files_read: int = DEFAULT_MAX_FILES_READ,
    max_chars_read: int = DEFAULT_MAX_CHARS_READ,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    allow_llm_summary: bool = False,
) -> dict[str, Any]:
    """Run the Explorer research layer and return an ExplorerBrief.

    Args:
        target_key: key into MAW targets.json.
        prompt: the user's task description.
        max_files_read: max number of candidate files to examine.
        max_chars_read: max total chars to read across all files.
        timeout_seconds: max wall-clock time for the explorer run.
        allow_llm_summary: if True, use a fast model to generate a summary
            (not implemented in Phase 6f-A — reserved).

    Returns:
        An ExplorerBrief dict matching the Phase 6f schema.
    """
    brief: dict[str, Any] = {
        "version": 1,
        "status": "ready",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "targetKey": target_key,
        "query": prompt,
        "previewKey": {"targetKey": target_key, "prompt": prompt},
        "summary": "",
        "relevantAreas": [],
        "candidateFiles": [],
        "missingContext": [],
        "commands": [],
        "limits": {
            "maxFilesRead": max_files_read,
            "maxCharsRead": max_chars_read,
            "timeoutSeconds": timeout_seconds,
            "filesRead": 0,
            "charsRead": 0,
            "hitTimeout": False,
        },
        "accessIssues": [],
    }

    # G2: empty prompt → skip.
    if not prompt or not prompt.strip():
        brief["status"] = "skipped"
        brief["accessIssues"].append({"path": "<explorer>", "reason": "skipped:empty_prompt"})
        return brief

    # Resolve target root.
    targets = load_targets()
    projects = targets.get("projects", {})
    if target_key not in projects:
        raise ContextTargetError(f"Unknown target key: '{target_key}'")
    target_path = projects[target_key].get("path", "")
    root = Path(get_project_root(target_path))
    if not root.is_dir():
        raise ContextTargetError(f"Target directory does not exist: {root}")

    # Use a result holder and threading for timeout.
    result_holder: dict[str, Any] = {"done": False}

    def _run_explorer():
        try:
            _explorer_core(
                brief, target_key, prompt, root,
                max_files_read, max_chars_read,
            )
        except Exception as e:
            logger.exception("Explorer core failed")
            brief["status"] = "failed"
            brief["accessIssues"].append({"path": "<explorer>", "reason": f"internal_error: {e}"})
        finally:
            result_holder["done"] = True

    thread = threading.Thread(target=_run_explorer, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if not result_holder.get("done"):
        brief["status"] = "timeout"
        brief["summary"] = ""
        brief["limits"]["hitTimeout"] = True
        brief["accessIssues"].append({
            "path": "<explorer>",
            "reason": f"timeout:{timeout_seconds}s",
        })
        # Thread continues in background but we ignore its results.

    return brief


def _explorer_core(
    brief: dict[str, Any],
    target_key: str,
    prompt: str,
    root: Path,
    max_files_read: int,
    max_chars_read: int,
) -> None:
    """Core explorer logic (runs inside the timeout-guarded thread)."""

    # Phase 1: derive search scope from scout suggestions.
    search_dirs, scout_hits = _get_search_scope(target_key, prompt, root)

    if not search_dirs:
        brief["status"] = "partial"
        brief["accessIssues"].append({
            "path": "<explorer>",
            "reason": "no_search_dirs_derived",
        })
        return

    # Phase 2: text search for prompt keywords.
    keywords = _extract_explorer_keywords(prompt)
    search_query = " ".join(keywords[:6]) if keywords else prompt[:80]
    has_rg = _detect_rg()
    rg_timeout = min(10, brief["limits"]["timeoutSeconds"] - 1)

    if has_rg:
        search_lines, search_duration = _search_with_rg(
            search_query, search_dirs, root, timeout=rg_timeout,
        )
    else:
        search_lines, search_duration = _search_with_python(
            search_query, search_dirs, root,
        )

    brief["commands"].append({
        "kind": "search_text",
        "query": search_query[:200],
        "tool": "rg" if has_rg else "python",
        "pathsSearched": len(search_dirs),
        "resultCount": len(search_lines),
        "durationMs": round(search_duration),
    })

    # Phase 3: read candidate files (defensive, scope-limited).
    candidate_paths: list[str] = []
    seen_candidates: set[str] = set()

    # Add scout hits first.
    for h in scout_hits:
        if len(candidate_paths) >= max_files_read:
            break
        p = h["path"]
        if p not in seen_candidates and (root / p).is_file():
            candidate_paths.append(p)
            seen_candidates.add(p)

    # Add search hit files.
    if search_lines:
        for line in search_lines[:20]:
            if len(candidate_paths) >= max_files_read:
                break
            filename = line.split(":", 1)[0]
            if filename and filename not in seen_candidates:
                fp = root / filename
                if fp.is_file():
                    # G5: skip secrets.
                    excluded, reason = _is_always_excluded(fp, root)
                    if excluded:
                        brief["accessIssues"].append({
                            "path": _mask_secret_path(filename),
                            "reason": reason,
                        })
                        continue
                    candidate_paths.append(filename)
                    seen_candidates.add(filename)

    # Phase 4: read candidate files.
    chars_remaining = max_chars_read
    for cpath in candidate_paths:
        if chars_remaining <= 0:
            break
        fp = root / cpath
        per_file = min(6000, chars_remaining)
        content = _defensive_read(fp, per_file)
        chars = len(content)
        brief["limits"]["filesRead"] += 1
        brief["limits"]["charsRead"] += chars
        chars_remaining -= chars

        truncated = chars >= per_file

        # Extract a short excerpt (first 3 non-empty non-comment lines).
        excerpt_lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")][:3]
        excerpt = "\n".join(excerpt_lines)[:300]

        reason = "search_hit" if any(cpath in sl for sl in search_lines) else "scout_suggestion"
        evidence: list[str] = []
        for sl in search_lines:
            if sl.startswith(cpath):
                evidence.append(f"search_hit:{sl.split(':', 2)[2][:80] if ':' in sl[sl.index(':', 2)+1:] else ''}")

        brief["candidateFiles"].append({
            "path": cpath,
            "reason": reason,
            "evidence": evidence[:5],
            "contentIncluded": True,
            "charsRead": chars,
            "truncated": truncated,
            "excerpt": excerpt,
        })

    # Phase 5: derive relevant areas.
    area_dirs: dict[str, dict[str, Any]] = {}
    for cf in brief["candidateFiles"]:
        dirname = cf["path"].rsplit("/", 1)[0] if "/" in cf["path"] else "."
        if dirname not in area_dirs:
            evidence_count = sum(1 for sl in search_lines if sl.startswith(cf["path"]))
            area_dirs[dirname] = {
                "path": dirname,
                "reason": f"Contains files relevant to: {prompt[:60]}",
                "confidence": "high" if any(cf["path"] == h["path"] for h in scout_hits) else "medium",
                "evidence": [f"search_hit:{prompt[:30]}:{evidence_count}"] if evidence_count else [],
            }
    brief["relevantAreas"] = list(area_dirs.values())[:5]

    # Phase 6: build summary.
    summary_parts: list[str] = []
    num_files = len(brief["candidateFiles"])
    if num_files:
        file_list = ", ".join(cf["path"] for cf in brief["candidateFiles"][:3])
        summary_parts.append(f"Explorer examined {num_files} candidate files: {file_list}.")
    if brief["relevantAreas"]:
        area_list = ", ".join(a["path"] for a in brief["relevantAreas"][:3])
        summary_parts.append(f"Relevant areas: {area_list}.")
    if brief["commands"]:
        cmd = brief["commands"][0]
        summary_parts.append(
            f"Searched for '{cmd['query']}' across {cmd['pathsSearched']} paths "
            f"({cmd['resultCount']} hits, {cmd['durationMs']}ms)."
        )

    if not summary_parts:
        brief["status"] = "partial"
        summary_parts.append("Explorer found no relevant files or areas.")

    brief["summary"] = " ".join(summary_parts)

    # G7/G8: mark truncation.
    if brief["limits"]["filesRead"] >= max_files_read and len(seen_candidates) > max_files_read:
        brief["accessIssues"].append({
            "path": "<explorer>",
            "reason": f"truncated:max_files_read:{max_files_read}",
        })
        brief["status"] = "partial"
    if brief["limits"]["charsRead"] >= max_chars_read:
        brief["accessIssues"].append({
            "path": "<explorer>",
            "reason": f"truncated:max_chars_read:{max_chars_read}",
        })
        if brief["status"] == "ready":
            brief["status"] = "partial"


def _extract_explorer_keywords(prompt: str) -> list[str]:
    """Extract meaningful keywords for explorer text search."""
    # Simple tokenization: split on non-alpha, keep 3+ char tokens, deduplicate.
    words = re.findall(r"[a-zA-Z_]{3,}", prompt.lower())
    stop = {
        "the", "and", "for", "are", "not", "you", "all", "can", "has",
        "was", "were", "that", "this", "with", "from", "will", "have",
        "been", "when", "them", "than", "then", "also", "just", "like",
        "very", "into", "over", "such", "only", "other", "more", "some",
        "would", "could", "should", "about", "after", "before", "each",
        "every", "which", "their", "there", "where", "what", "make",
        "made", "need", "want", "file", "files", "code", "please",
        "implement", "create", "change", "update", "fix", "remove",
        "delete", "test", "check",
    }
    seen: set[str] = set()
    keywords: list[str] = []
    for w in words:
        if w not in stop and w not in seen:
            seen.add(w)
            keywords.append(w)
    return keywords
