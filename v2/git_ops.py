"""v2 git_ops — minimal git safety checks for executor operations."""

import subprocess
from pathlib import Path
from typing import Optional

def git_root(path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

def current_branch(path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

def is_clean(path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == ""
    except Exception:
        return False

def latest_commit_sha(path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

def has_uncommitted(path: str) -> bool:
    return not is_clean(path)

def validate_target(path: str) -> tuple[bool, list[str]]:
    """Basic target validation: directory exists, is a git repo."""
    issues = []
    p = Path(path).resolve()
    if not p.is_dir():
        issues.append(f"Path does not exist: {p}")
        return False, issues
    if not git_root(str(p)):
        issues.append(f"Not a git repository: {p}")
    return len(issues) == 0, issues
