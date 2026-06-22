"""L2 Scout: prompt-based safe-file suggestion engine.

Phase 6e-A: recommendations only — never auto-injected into Council.
No embeddings, no vector DB, no new dependencies.
Read-only, safe-file-only, target-root-confined.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import project_context as pc
from maw_paths import get_project_root

logger = logging.getLogger(__name__)

# Keywords that indicate a filename-like mention in prompts.
_FILENAME_PATTERN = re.compile(
    r'\b[\w./-]*\.(?:py|js|ts|jsx|tsx|go|rs|java|rb|php|c|cpp|h|hpp|css|scss|html|vue|svelte|md|json|yml|yaml|toml|xml|sql|sh|bash|zsh|tf|dockerfile|makefile|cmake)\b',
    re.IGNORECASE,
)

# Words that are too common to be useful as content-match keywords.
_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "to",
    "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above",
    "below", "between", "under", "over", "and", "but", "or",
    "not", "no", "if", "then", "else", "when", "where", "why",
    "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "only", "own", "same", "so", "than",
    "too", "very", "just", "now", "also", "this", "that", "these",
    "those", "it", "its", "add", "use", "make", "get", "set",
    "put", "need", "want", "like", "new", "old", "good", "bad",
    "first", "last", "next", "implement", "create", "change",
    "update", "fix", "remove", "delete", "test", "run", "check",
    "build", "deploy", "please", "code", "file", "files",
    "project", "feature", "request", "issue",
}

# Maximum file size for content scanning (bytes).
_MAX_CONTENT_SCAN_BYTES = 50000
# Cap how many files receive a content scan per scout run.
_MAX_CONTENT_SCAN_FILES = 15


def _extract_filename_tokens(prompt: str) -> list[str]:
    """Extract filename-like tokens from a prompt.

    Returns relative paths (lowercased, stripped).
    """
    matches = _FILENAME_PATTERN.findall(prompt)
    seen: set[str] = set()
    results: list[str] = []
    for m in matches:
        token = m.strip().lstrip("./\\").lower()
        if token and token not in seen:
            seen.add(token)
            results.append(token)
    return results


def _extract_keywords(prompt: str) -> list[str]:
    """Extract meaningful keyword tokens from a prompt.

    Splits on non-alpha, filters stop words, deduplicates.
    """
    words = re.findall(r'[a-zA-Z_]{3,}', prompt.lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for w in words:
        if w not in _STOP_WORDS and w not in seen:
            seen.add(w)
            keywords.append(w)
    return keywords


def _path_components_match(filename_token: str, file_path: str) -> bool:
    """Check if the filename token matches any path component of file_path."""
    norm_path = file_path.lower().replace("\\", "/")
    parts = norm_path.split("/")
    # Match against full path or individual components.
    if filename_token in norm_path:
        return True
    for part in parts:
        if part == filename_token or part.startswith(filename_token) or filename_token.startswith(part):
            return True
    return False


def _safe_read_head(path: Path, max_bytes: int = 8000) -> str:
    """Read the first max_bytes of a file, safely.

    Returns empty string on any error.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
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


def _test_file_nearby(file_path: str, all_paths: set[str]) -> bool:
    """Check if a test file exists next to a matched source file.

    E.g., if file_path is "src/auth/login.py", check for
    "src/auth/test_login.py" or "tests/auth/test_login.py".
    """
    norm = file_path.lower()
    parts = norm.rsplit("/", 1)
    if len(parts) != 2:
        return False
    dirname, basename = parts
    stem, ext = os.path.splitext(basename)

    # Common test naming patterns.
    candidates = [
        f"{dirname}/test_{basename}",
        f"{dirname}/{stem}_test{ext}",
        f"tests/{dirname}/test_{basename}" if dirname else f"tests/test_{basename}",
        f"test/{dirname}/test_{basename}" if dirname else f"test/test_{basename}",
        f"tests/test_{basename}",
        f"tests/{basename}",
    ]
    for c in candidates:
        if c.lower() in all_paths:
            return True
    return False


def scout_suggestions(
    target_key: str,
    prompt: str,
    max_results: int = 8,
) -> list[dict[str, Any]]:
    """Analyze a prompt and return scored file recommendations.

    Args:
        target_key: key into MAW targets.json.
        prompt: the user's task description.
        max_results: maximum number of suggestions to return.

    Returns:
        List of suggestion dicts with keys: path, score, reasons, size, kind.
        Never includes secret/binary/gitignored/build/vendor files.
    """
    safe_files = pc.list_safe_files(target_key)
    if not safe_files:
        return []

    targets = pc.load_targets()
    target_path = targets.get("projects", {}).get(target_key, {}).get("path", "")
    root_str = get_project_root(target_path) if target_path else ""
    project_root = Path(root_str) if root_str else None

    all_paths = {f["path"].lower() for f in safe_files}

    # Phase 1: parse prompt.
    filename_tokens = _extract_filename_tokens(prompt)
    keywords = _extract_keywords(prompt)

    # Phase 2: score every safe file.
    scored: list[dict[str, Any]] = []
    content_scans = 0
    for f in safe_files:
        rel_path = f["path"]
        score = 0
        reasons: list[str] = []

        # (A) Exact filename match.
        for ft in filename_tokens:
            norm = rel_path.lower()
            if norm == ft or norm.endswith("/" + ft):
                score += 100
                reasons.append(f"filename_match:{ft}")
                break
            elif _path_components_match(ft, rel_path):
                score += 60
                reasons.append(f"path_match:{ft}")
                break

        # (B) Keyword match in path.
        for kw in keywords:
            if kw in rel_path.lower().replace("/", " ").replace("_", " ").replace("-", " ").replace(".", " "):
                score += 25
                reasons.append(f"keyword_in_path:{kw}")
                break  # One keyword match per file is enough.

        # (C) Content keyword match (for small files only, capped per run).
        if (
            not reasons
            and keywords
            and project_root is not None
            and f.get("size", 0) < _MAX_CONTENT_SCAN_BYTES
            and content_scans < _MAX_CONTENT_SCAN_FILES
        ):
            content_scans += 1
            abs_path = project_root / rel_path
            head = _safe_read_head(abs_path, 8000)
            if head:
                head_lower = head.lower()
                content_matches = sum(1 for kw in keywords if kw in head_lower)
                if content_matches:
                    score += 30 + content_matches * 5
                    reasons.append(f"content_match:{content_matches}kws")

        if not reasons:
            continue

        # (D) Test file bonus.
        if _test_file_nearby(rel_path, all_paths):
            score += 20
            reasons.append("test_file_nearby")

        scored.append({
            "path": rel_path,
            "score": score,
            "reasons": reasons,
            "size": f.get("size", 0),
            "kind": f.get("kind", "unknown"),
        })

    # Sort by score descending, then by path.
    scored.sort(key=lambda x: (-x["score"], x["path"]))
    return scored[:max_results]
